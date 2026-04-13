"""JarvisOrchestrator — the consciousness of Jarvis.

Routes user messages and system events to the right sub-agents,
manages traces, enforces budgets, and coordinates the intelligence loop.
This is the main entry point for all Jarvis interactions.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.services.relevance_assessor import PerceptionSignal, RelevanceAssessment

from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.orchestrator.agent_loop import (
    LoopAgentStart,
    LoopDone,
    LoopError,
    LoopTextDelta,
    LoopThinking,
    LoopToolCall,
    LoopToolResult,
    agent_loop,
)
from src.orchestrator.agents import AGENTS, SubAgent
from src.orchestrator.budget import BudgetTracker
from src.orchestrator.contracts import PlanOutput, PlanStep
from src.orchestrator.intent_classifier import (
    FAST_INTENTS,
    INTENT_CONFIDENCE_THRESHOLD,
    classify_intent,
    extract_plan,
    intent_to_plan,
)
from src.orchestrator.prompts import JARVIS_SOUL_CORE
from src.orchestrator.services import ServiceContainer
from src.orchestrator.tracing import TraceManager
from src.services.agent_registry import AgentRegistry
from src.services.capability_resolver import CapabilityResolver, route_step
from src.services.context_builder import ContextBuilder, ContextPack
from src.services.interaction_learner import InteractionLearner
from src.services.surface_mapping import (
    build_surface_preview_from_plan,
    derive_surface_kind,
    extract_surface_spec,
)
from src.services.trace_store import TraceStore
from src.tools.schemas import build_tool_definitions

logger = logging.getLogger(__name__)

# Event types published to the agent events stream
AGENT_EVENT_TYPES = {
    "plan_generated",
    "approval_requested",
    "execution_started",
    "execution_completed",
    "memory_updated",
    "entity_created",
    "briefing_generated",
    "perception_completed",
}

# Model IDs for each tier (direct API)
MODEL_TIERS = {
    "opus": "claude-opus-4-20250514",
    "sonnet": "claude-sonnet-4-20250514",
    "haiku": "claude-haiku-4-20250514",
}

# Bedrock inference profile IDs (cross-region, works in ap-south-1)
BEDROCK_MODEL_TIERS = {
    "opus": "global.anthropic.claude-opus-4-5-20251101-v1:0",
    "sonnet": "apac.anthropic.claude-sonnet-4-20250514-v1:0",
    "haiku": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}

# Agents that benefit from context enrichment (read-heavy agents)
CONTEXT_ENRICHED_AGENTS = {
    "planner",
    "presenter",
    "perceiver",
    "librarian",
    "operator",
    "governor",
}

# Intent classification constants imported from intent_classifier module


async def _fetch_thread_contexts(
    raw_events: list,
    user_id: str,
    workspace_id: str,
    max_threads: int = 3,
) -> dict[str, dict]:
    """Fetch full thread context for Gmail reply events via MCP.

    When a reply arrives on a Gmail thread, the Librarian/Planner only see
    the reply snippet with no context about the prior conversation. This
    helper fetches the full thread via the ``get_gmail_thread_content`` MCP
    tool so downstream agents can reason over the complete thread.

    Returns a mapping of ``{thread_id: mcp_result}`` for successfully
    fetched threads. On any failure the thread is silently skipped so
    perception is never blocked by MCP availability.
    """
    from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

    contexts: dict[str, dict] = {}
    if not is_mcp_tool("get_gmail_thread_content"):
        return contexts

    fetched = 0
    seen: set[str] = set()
    for raw_evt in raw_events:
        if fetched >= max_threads:
            break
        if raw_evt.source != "gmail":
            continue
        payload = raw_evt.raw_payload or {}
        in_reply_to = payload.get("in_reply_to", "")
        thread_id = raw_evt.entity_id
        if not in_reply_to or thread_id in seen:
            continue
        seen.add(thread_id)

        try:
            result = await call_mcp_tool(
                "get_gmail_thread_content",
                {"thread_id": thread_id},
                user_id=user_id,
                workspace_id=workspace_id,
            )
            if isinstance(result, dict) and result.get("status") != "error":
                contexts[thread_id] = result
                fetched += 1
        except Exception:
            logger.debug("Failed to fetch thread %s context", thread_id, exc_info=True)

    return contexts


def _build_step_to_task_map(steps: list) -> dict[str, str]:
    """First pass: create step_id -> task_id mapping for ALL steps.

    Pre-builds the full mapping so that forward dependencies (e.g. step s1
    depends on s3, which appears later in the list) resolve correctly.
    Includes both jarvis and user actor steps since user steps can be
    dependency targets.
    """
    step_to_task: dict[str, str] = {}
    for step in steps:
        if step.step_id:
            step_to_task[step.step_id] = f"ptask_{ULID()}"
    return step_to_task


def _build_action_preview(capability: str, description: str) -> str:
    """Generate tooltip preview text for an insight action based on capability type."""
    cap = capability.lower()
    if any(w in cap for w in ("send", "create", "update", "delete", "write")):
        return f"Creates a task to {description.lower()}"
    if any(w in cap for w in ("read", "search", "fetch", "list", "get")):
        return f"Fetches {capability.split('.')[-1]} data without taking action"
    if any(w in cap for w in ("respond", "reason", "summarize")):
        return f"Generates a response about {description.lower()}"
    return ""


class JarvisOrchestrator:
    """The Jarvis brain — orchestrates sub-agents via Claude API.

    This is NOT a ClaudeSDKClient wrapper (SDK not yet stable enough).
    Instead, we use the Anthropic API directly with structured prompts
    to simulate sub-agent routing. Each sub-agent call is a separate
    Claude API call with the agent's specific prompt and tool scope.
    """

    def __init__(
        self,
        settings: Settings,
        db_factory,
        services: ServiceContainer,
    ):
        self._settings = settings
        self._db_factory = db_factory
        self._services = services
        self._client = get_anthropic_client(settings)
        self._trace_store = TraceStore(db_factory=db_factory)
        self._trace_manager = TraceManager(trace_store=self._trace_store)
        self._budget = BudgetTracker(
            daily_limit_usd=settings.daily_token_budget_usd,
            redis=getattr(services, "redis", None) if services else None,
        )
        self._agents: dict[str, SubAgent] = dict(AGENTS)  # Start with hardcoded defaults
        self._tools = self._build_tool_definitions()
        self._event_bus = None  # Lazy-init when Redis available
        self._event_bus_lock = asyncio.Lock()  # C5: guard lazy EventBus init
        self._background_tasks: set[asyncio.Task] = set()  # C2: track fire-and-forget tasks
        # C1: API circuit breaker — fail fast when Claude API is in sustained outage
        from src.orchestrator.api_circuit_breaker import AnthropicCircuitBreaker

        self._circuit_breaker = AnthropicCircuitBreaker()
        # Interaction learning — async memory extraction from user messages
        self._interaction_learner: InteractionLearner | None = None
        if self._services.memory_service:
            self._interaction_learner = InteractionLearner(
                settings=settings,
                db_factory=db_factory,
                vector_store=self._services.vector_store,
                redis=None,  # Populated lazily when event bus Redis is available
            )
        # Precompute haiku model ID for intent classification
        if settings.use_bedrock:
            self._haiku_model = BEDROCK_MODEL_TIERS["haiku"]
        else:
            self._haiku_model = MODEL_TIERS["haiku"]

    def _spawn_background(self, coro) -> None:
        """Launch a background task with lifecycle tracking (C2)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _ensure_learner_redis(self) -> None:
        """Ensure the interaction learner has a Redis connection."""
        if self._interaction_learner and self._interaction_learner._redis is None:
            event_bus = await self._ensure_event_bus()
            if event_bus and hasattr(self, "_event_bus_redis"):
                self._interaction_learner._redis = self._event_bus_redis

    async def shutdown(self) -> None:
        """Await all pending background tasks on orchestrator shutdown."""
        if self._background_tasks:
            logger.info("Awaiting %d background tasks", len(self._background_tasks))
            await asyncio.wait(self._background_tasks, timeout=5.0)

    async def get_budget_status(self):
        """Public accessor for budget status — replaces private _budget access."""
        async with self._db_factory() as db:
            return await self._budget.get_budget_status(db)

    async def get_system_health(self) -> dict:
        """Public accessor for system health — replaces private attribute access."""
        return {
            "circuit_breaker_open": (
                self._circuit_breaker.is_open()
                if hasattr(self._circuit_breaker, "is_open")
                else False
            ),
            "background_tasks": len(self._background_tasks),
            "agents": sorted(self._agents.keys()),
        }

    async def _get_available_capabilities(self, workspace_id: str) -> list[str]:
        """Get list of available capability strings from the tool registry."""
        try:
            async with self._db_factory() as db:
                resolver = CapabilityResolver(db, workspace_id)
                tools = await resolver._list_enabled_tools()
                return list({t.capability for t in tools if t.capability})
        except Exception:
            logger.debug("Failed to get available capabilities", exc_info=True)
            return []

    async def load_agents_from_db(self) -> None:
        """Load agent definitions from the database, replacing hardcoded defaults."""
        try:
            async with self._db_factory() as db:
                registry = AgentRegistry(db)
                await registry.seed_defaults()
                await db.commit()
                db_agents = await registry.load_as_sub_agents()
                if db_agents:
                    self._agents = db_agents
                    logger.info(
                        "Loaded %d agents from DB: %s",
                        len(db_agents),
                        sorted(db_agents.keys()),
                    )
        except Exception:
            logger.debug("Agent DB load failed, using hardcoded defaults", exc_info=True)

    async def _persist_plan_record(
        self,
        plan_output: PlanOutput,
        user_id: str,
        workspace_id: str,
        trigger_type: str = "user_message",
        idempotency_key: str | None = None,
    ) -> PlanOutput:
        """Persist a Plan + PlanTasks to DB, returning PlanOutput with plan_id set.

        Converts PlanOutput steps into DB Plan + PlanTask rows so the Governor
        can evaluate_policy(plan_id) and the Operator can execute via
        GraphExecutor — both require a DB-backed Plan.

        Both ``jarvis`` and ``user`` actor steps become PlanTasks. User-actor
        steps are persisted with ``task_type="user_action"`` and
        ``status="awaiting_input"`` so they appear as dependency targets and
        in execution surfaces.

        A two-pass approach pre-builds step_id→task_id mappings so forward
        dependencies (e.g. s1 depends on s3) resolve correctly.

        Args:
            trigger_type: Origin — "user_message" (interactive) or "perception"
                          (autonomous observation).
            idempotency_key: Optional dedup key to prevent duplicate perception plans.
        """
        from src.models.plans import Plan, PlanTask

        plan_id = f"plan_{ULID()}"

        # Risk ordinals for deriving max risk
        risk_ord: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
        ord_risk: dict[int, str] = {v: k for k, v in risk_ord.items()}

        try:
            async with self._db_factory() as db:
                # Idempotency check — skip if an active plan with this key exists
                if idempotency_key:
                    from sqlalchemy import select

                    existing = await db.execute(
                        select(Plan.plan_id).where(
                            Plan.idempotency_key == idempotency_key,
                            Plan.workspace_id == workspace_id,
                            Plan.status.notin_(["completed", "failed", "cancelled"]),
                        )
                    )
                    existing_plan_id = existing.scalar_one_or_none()
                    if existing_plan_id:
                        logger.info(
                            "Skipping duplicate plan: idempotency_key=%s",
                            idempotency_key,
                        )
                        return plan_output.model_copy(update={"plan_id": existing_plan_id})

                # Pass 1: Pre-build step_id → task_id map for ALL steps
                # so forward dependencies resolve correctly.
                step_to_task = _build_step_to_task_map(plan_output.steps)

                # Pass 2: Create PlanTask records for every step.
                tasks: list[PlanTask] = []
                max_risk_ord = 0

                for step in plan_output.steps:
                    max_risk_ord = max(max_risk_ord, risk_ord.get(step.risk, 0))

                    # Reuse the pre-assigned task_id (or generate one for
                    # steps without a step_id).
                    task_id = step_to_task.get(step.step_id, f"ptask_{ULID()}")

                    # Map step depends_on step_ids to task_ids
                    dep_task_ids = [
                        step_to_task[dep] for dep in step.depends_on if dep in step_to_task
                    ]

                    if step.actor == "user":
                        tasks.append(
                            PlanTask(
                                task_id=task_id,
                                plan_id=plan_id,
                                workspace_id=workspace_id,
                                task_type="user_action",
                                input_data={
                                    "description": step.description,
                                    "capability": step.capability,
                                },
                                depends_on=dep_task_ids or None,
                                status="awaiting_input",
                            )
                        )
                    else:
                        step_input = dict(step.input) if step.input else {}
                        if step.description:
                            step_input["description"] = step.description
                        if step.capability:
                            step_input["capability"] = step.capability
                        tasks.append(
                            PlanTask(
                                task_id=task_id,
                                plan_id=plan_id,
                                workspace_id=workspace_id,
                                task_type=step.capability,
                                input_data=step_input,
                                depends_on=dep_task_ids or None,
                                status="pending",
                            )
                        )

                # Derive risk_level and execution_mode from max step risk
                risk_level = ord_risk.get(max_risk_ord, "low")
                execution_mode = "approval_required" if max_risk_ord >= 2 else "auto_execute"

                plan_record = Plan(
                    plan_id=plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    trigger_type=trigger_type,
                    trigger_ref=None,
                    idempotency_key=idempotency_key,
                    goal=plan_output.goal or "",
                    priority=plan_output.priority,
                    decision="plan",
                    reasoning_summary=plan_output.reasoning or None,
                    risk_level=risk_level,
                    execution_mode=execution_mode,
                    status="created",
                    success_conditions=(
                        {"criteria": plan_output.success_criteria}
                        if plan_output.success_criteria
                        else None
                    ),
                    plan_output_json=plan_output.model_dump(mode="json"),
                )
                plan_record.tasks = tasks
                db.add(plan_record)
                await db.commit()

            logger.info(
                "Persisted plan %s tasks=%d risk=%s",
                plan_id,
                len(tasks),
                risk_level,
            )
            return plan_output.model_copy(update={"plan_id": plan_id})
        except Exception:
            logger.warning("Failed to persist plan record", exc_info=True)
            return plan_output

    async def _log_interaction(
        self,
        user_id: str,
        workspace_id: str,
        trace_id: str,
        message_preview: str | None = None,
        intent: str | None = None,
        plan: "PlanOutput | None" = None,
        conversation_id: str | None = None,
        response_preview: str | None = None,
        run_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
    ) -> str | None:
        """Create a lightweight InteractionLog record for auditing.

        Replaces _create_lightweight_run + _complete_lightweight_run.
        Returns the interaction_id on success, None on failure.
        """
        from src.models.interaction_log import InteractionLog

        interaction_id = f"ilog_{ULID()}"
        try:
            async with self._db_factory() as db:
                db.add(
                    InteractionLog(
                        interaction_id=interaction_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        trace_id=trace_id,
                        conversation_id=conversation_id,
                        message_preview=(message_preview[:500] if message_preview else None),
                        plan_summary=(plan.reasoning[:500] if plan and plan.reasoning else None),
                        plan_id=plan.plan_id if plan else None,
                        run_id=run_id,
                        intent=intent,
                        response_preview=(response_preview[:500] if response_preview else None),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost_usd,
                        latency_ms=latency_ms,
                    )
                )
                await db.commit()
        except Exception:
            logger.warning("Failed to log interaction", exc_info=True)
            return None
        return interaction_id

    def _build_tool_definitions(self) -> list[dict]:
        """Build workspace-independent tool definitions (internal + native connectors).

        MCP tools are workspace-scoped and merged at call time via
        _get_tools_for_agent(workspace_id=...).
        """
        tools = self._build_internal_tool_definitions()

        # Composite web_search tool (uses Playwright MCP internally)
        tools.append(
            {
                "name": "web_search",
                "description": (
                    "Search the web using DuckDuckGo via a headless browser. "
                    "Returns structured results with titles, URLs, and snippets. "
                    "Use this when you need to find information on the web."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "num_results": {
                            "type": "integer",
                            "description": "Max results to return (default 10, max 20)",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            }
        )

        return tools

    def _build_internal_tool_definitions(self) -> list[dict]:
        """Build Claude tool definitions from Pydantic models in tool_schemas."""
        return build_tool_definitions()

    @staticmethod
    def _internal_tool_names() -> set[str]:
        """Return the set of internal (non-MCP) tool names."""
        from src.tools.schemas import TOOL_INPUT_MODELS

        return set(TOOL_INPUT_MODELS.keys())

    async def _get_tools_for_agent(self, agent: SubAgent, workspace_id: str = "") -> list[dict]:
        """Build tool list from DB registry, filtered by agent capability scope.

        Internal tools come from build_tool_definitions() (Pydantic schemas).
        External tools come from ToolDefinition DB records (seeded + discovered).
        Session pool metadata enriches schema/description when DB records lack them.
        """
        from src.connectors.mcp_bridge import list_mcp_tools
        from src.services.tool_registry import ToolRegistry

        scope = agent.capability_scope
        if not scope:
            return []

        # Start with internal tools, filtered by capability
        tools: list[dict] = []
        internal_names: set[str] = set()

        async with self._db_factory() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)

            for t in self._tools:
                tool_def = await registry.get_tool(t["name"])
                if tool_def and tool_def.capability and tool_def.capability in scope:
                    tools.append(t)
                    internal_names.add(t["name"])

            # Build schema lookup from session pool (enriches DB records
            # that lack input_schema — e.g., external seeds before discovery)
            mcp_schemas: dict[str, dict] = {}
            for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
                mcp_schemas[mcp_tool["name"]] = {
                    "description": mcp_tool.get("description", ""),
                    "input_schema": mcp_tool.get("input_schema", {}),
                }

            # Add external tools from DB registry, filtered by capability
            all_db_tools = await registry.list_tools(enabled_only=True)

            for tool_def in all_db_tools:
                if tool_def.name in internal_names:
                    continue
                if not tool_def.capability or tool_def.capability not in scope:
                    continue

                # Live MCP schemas take priority for external tools — the
                # MCP server is the source of truth (e.g., OAuth 2.1 mode
                # strips user_google_email from schemas at runtime).
                # Fallback to DB schema. Skip tools with no schema from any
                # real source — presenting tools with empty schemas causes
                # agents to call them without required params.
                schema = None
                description = tool_def.description or tool_def.name

                if tool_def.name in mcp_schemas:
                    schema = mcp_schemas[tool_def.name].get("input_schema")
                    live_desc = mcp_schemas[tool_def.name].get("description")
                    if live_desc:
                        description = live_desc

                if not schema:
                    schema = tool_def.input_schema

                if not schema:
                    logger.debug(
                        "Skipping tool %s — no schema from MCP or DB yet",
                        tool_def.name,
                    )
                    continue

                tools.append(
                    {
                        "name": tool_def.name,
                        "description": description,
                        "input_schema": schema,
                    }
                )

        return tools

    def _get_model_for_agent(self, agent: SubAgent) -> str:
        """Get the Claude model ID for an agent's tier."""
        if self._settings.use_bedrock:
            return BEDROCK_MODEL_TIERS.get(agent.model_tier, BEDROCK_MODEL_TIERS["sonnet"])
        return MODEL_TIERS.get(agent.model_tier, MODEL_TIERS["sonnet"])

    async def process_message(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        conversation_id: str | None = None,
        surface: str = "api",
        context: dict | None = None,
    ) -> dict:
        """Process a user message through the orchestrator.

        Routes through capability-based plan steps:
        1. Classify intent (Haiku fast path or full Planner)
        2. Generate PlanOutput with capability steps
        3. Pre-resolve agent routing and tools for each step
        4. Execute steps sequentially
        5. Presenter formats the final response
        """
        if not user_id:
            return {"error": "user_id is required"}
        if not workspace_id:
            return {"error": "workspace_id is required"}
        if not message or not message.strip():
            return {"error": "Empty message"}

        trace = self._trace_manager.start_trace("user_message")

        try:
            await self._emit_runtime_event(
                "command_received",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"surface": surface, "message_preview": message[:100]},
            )

            history_block = await self._load_conversation_history(conversation_id, user_id=user_id)

            # Step 0: Fast intent classification
            intent, confidence, sources = await classify_intent(
                self._client, self._haiku_model, message, history_block
            )
            use_planner = intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD

            if sources:
                await self._bump_perception_for_sources(sources, user_id, workspace_id)

            await self._emit_runtime_event(
                "route_selected",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={
                    "intent": intent,
                    "confidence": confidence,
                    "use_planner": use_planner,
                },
            )

            # Step 1: Generate PlanOutput
            plan: PlanOutput
            plan_text = ""

            if use_planner:
                planner_message = f"User message: {message}\n\nContext: {json.dumps(context or {})}"
                if history_block:
                    planner_message = f"{history_block}\n\n{planner_message}"

                plan_text = await self._call_agent(
                    "planner",
                    message=planner_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
                plan = extract_plan(plan_text)
            else:
                capabilities = await self._get_available_capabilities(workspace_id)
                plan = intent_to_plan(intent, message, capabilities)

            # Persist Plan record if multi-step or has write risk
            if len(plan.steps) > 1 or any(s.risk not in ("none",) for s in plan.steps):
                import hashlib

                goal_hash = hashlib.sha256((plan.goal or "").encode()).hexdigest()[:16]
                plan = await self._persist_plan_record(
                    plan,
                    user_id,
                    workspace_id,
                    idempotency_key=f"user:{goal_hash}",
                )

            plan_dict = plan.model_dump(mode="json")

            # Log this interaction for auditing
            ilog_id = await self._log_interaction(
                user_id=user_id,
                workspace_id=workspace_id,
                trace_id=trace.trace_id,
                message_preview=message[:500],
                intent=intent,
                plan=plan,
                conversation_id=conversation_id,
            )

            result: dict[str, Any] = {
                "trace_id": trace.trace_id,
                "run_id": None,
                "interaction_id": ilog_id,
                "plan": plan_dict,
                "summary": plan.reasoning or plan_text,
            }

            await self._publish_event(
                "plan_generated",
                user_id,
                {"plan": plan_dict, "trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            # Step 2: Pre-resolve routing and tools for all steps
            step_routing: list[tuple[PlanStep, str, list[dict]]] = []
            user_steps: list[PlanStep] = []

            async with self._db_factory() as db:
                resolver = CapabilityResolver(db, workspace_id)
                for step in plan.steps:
                    if step.actor == "user":
                        user_steps.append(step)
                        continue
                    if step.capability.startswith("system."):
                        step_routing.append((step, "", []))
                    elif step.capability in ("reason", "respond"):
                        step_routing.append((step, "presenter", []))
                    elif step.capability == "perceive":
                        # Broad read: Perceiver gets ALL its tools, decides
                        # autonomously which sources to query.
                        step_routing.append((step, "perceiver", []))
                    else:
                        agent_name = await route_step(step.capability, resolver)
                        tools = await resolver.resolve_for_step(step.capability)
                        step_routing.append((step, agent_name, tools))

            # Step 3: Execute steps sequentially
            for step, agent_name, tools in step_routing:
                if step.capability.startswith("system."):
                    sys_result = await self._handle_system_capability(
                        step, plan, user_id, workspace_id
                    )
                    result[f"system_{step.capability}"] = sys_result
                    continue

                if not agent_name:
                    error_msg = f"No tools available for capability '{step.capability}'"
                    logger.warning(error_msg)
                    result[f"error_{step.step_id}"] = error_msg
                    continue

                agent_message = (
                    f"Execute this step: {step.description}\n"
                    f"Goal: {plan.goal}\n"
                    f"User message: {message}"
                )
                # Inject prior step results so downstream agents see earlier outputs
                prior_outputs = {
                    k: v
                    for k, v in result.items()
                    if k not in ("presentation", "user_actions") and v
                }
                if prior_outputs:
                    parts = []
                    for key, output in prior_outputs.items():
                        parts.append(f"[{key} output]:\n{str(output)[:3000]}")
                    agent_message += (
                        "\n\n--- Prior step results ---\n"
                        + "\n\n".join(parts)
                        + "\n--- End of prior step results ---\n"
                    )
                if history_block:
                    agent_message = f"{history_block}\n\n{agent_message}"

                agent_result = await self._call_agent(
                    agent_name,
                    message=agent_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                    tools_override=tools if tools else None,
                )
                result[agent_name] = agent_result

            # Build user action block from user_steps
            user_action_block = ""
            if user_steps:
                actions = "\n".join(
                    f"- {s.description}" + (f" ({s.user_context})" if s.user_context else "")
                    for s in user_steps
                )
                user_action_block = f"\n\nUser actions required:\n{actions}"
                result["user_actions"] = [
                    {"description": s.description, "context": s.user_context} for s in user_steps
                ]

            # Step 4: Presenter formats response — always call so user
            # gets a conversational answer (see streaming path comment).
            if True:
                # Collect prior step results so Presenter can reference them
                prior_results_block = ""
                step_outputs = {
                    k: v
                    for k, v in result.items()
                    if k not in ("presentation", "user_actions") and v
                }
                if step_outputs:
                    parts = []
                    for agent_key, output in step_outputs.items():
                        truncated = str(output)[:3000]
                        parts.append(f"[{agent_key} output]:\n{truncated}")
                    prior_results_block = (
                        "\n\n--- Prior step results (use these to answer the user) ---\n"
                        + "\n\n".join(parts)
                        + "\n--- End of prior step results ---\n"
                    )

                presenter_msg = (
                    f"Format this for the user ({surface}). "
                    f"Be conversational and helpful.\n\n"
                    f"User message: {message}\n"
                    f"Plan: {json.dumps(plan_dict)}"
                )
                if prior_results_block:
                    presenter_msg += prior_results_block
                if plan_text:
                    presenter_msg += f"\nAnalysis: {plan_text[:2000]}"
                if user_action_block:
                    presenter_msg += user_action_block
                if history_block:
                    presenter_msg = f"{history_block}\n\n{presenter_msg}"
                present_result = await self._call_agent(
                    "presenter",
                    message=presenter_msg,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
                result["presentation"] = present_result

            await self._emit_runtime_event(
                "run_completed",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=None,
                payload={"trace_id": trace.trace_id},
            )

            # Push surface to workspace (Presenter-driven)
            response_text = result.get("presentation", result.get("presenter", ""))
            surface_id = None
            try:
                surface_spec = extract_surface_spec(response_text)
                if surface_spec and surface_spec.should_surface:
                    surface_id = await self._push_presenter_surface(
                        spec=surface_spec,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        run_id=result.get("run_id"),
                        response_text=response_text,
                    )
            except Exception:
                logger.warning("Surface push failed", exc_info=True)
            if surface_id:
                result["surface_id"] = surface_id

            # Interaction learning (async, non-blocking)
            if self._interaction_learner:
                await self._ensure_learner_redis()
                self._spawn_background(
                    self._interaction_learner.learn(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        user_message=message,
                        agent_response=response_text,
                        intent=intent,
                        trace_id=trace.trace_id,
                    )
                )

            return result

        except Exception as e:
            logger.error("process_message failed: %s", e, exc_info=True)
            error_result = {
                "trace_id": trace.trace_id,
                "decision": "error",
                "summary": f"Error processing message: {e}",
            }
            await self._emit_runtime_event(
                "run_failed",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=None,
                payload={"error": str(e)[:200]},
            )
            return error_result
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def process_message_stream(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        surface: str = "web",
        mode: str = "ask",
        context: dict | None = None,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream events while processing a user message.

        Yields SSE-compatible dicts. Uses capability-based plan step routing.
        """
        if not user_id or not workspace_id:
            yield {
                "event": "error",
                "message": "user_id and workspace_id are required",
            }
            return
        if not message or not message.strip():
            yield {"event": "error", "message": "Empty message"}
            return

        trace = self._trace_manager.start_trace("user_message")

        def _fire_event(event_type: str, **kwargs: Any) -> None:
            self._spawn_background(self._emit_runtime_event(event_type, **kwargs))

        try:
            yield {"event": "trace", "trace_id": trace.trace_id}

            _fire_event(
                "command_received",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={
                    "surface": surface,
                    "message_preview": message[:100],
                },
            )

            history_block = await self._load_conversation_history(conversation_id, user_id=user_id)

            # Step 0: Fast intent classification
            intent, confidence, sources = await classify_intent(
                self._client, self._haiku_model, message, history_block
            )
            yield {
                "event": "intent",
                "intent": intent,
                "confidence": confidence,
            }

            if sources:
                await self._bump_perception_for_sources(sources, user_id, workspace_id)

            # Decide routing based on intent AND mode
            if mode in ("execute", "plan"):
                use_planner = True
            else:
                use_planner = intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD

            _fire_event(
                "route_selected",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={
                    "intent": intent,
                    "confidence": confidence,
                    "use_planner": use_planner,
                },
            )

            # Step 1: Generate PlanOutput
            plan: PlanOutput
            plan_text = ""

            if use_planner:
                planner_message = f"User message: {message}\n\nContext: {json.dumps(context or {})}"
                if history_block:
                    planner_message = f"{history_block}\n\n{planner_message}"

                async for evt in self._call_agent_stream(
                    "planner",
                    message=planner_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        plan_text = evt.get("text", "")

                plan = extract_plan(plan_text)
            else:
                capabilities = await self._get_available_capabilities(workspace_id)
                plan = intent_to_plan(intent, message, capabilities)

            # Apply mode overrides
            if mode == "plan":
                plan = plan.model_copy(update={"requires_user_input": True})

            # Persist Plan record if needed
            if len(plan.steps) > 1 or any(s.risk not in ("none",) for s in plan.steps):
                import hashlib

                goal_hash = hashlib.sha256((plan.goal or "").encode()).hexdigest()[:16]
                plan = await self._persist_plan_record(
                    plan,
                    user_id,
                    workspace_id,
                    idempotency_key=f"user:{goal_hash}",
                )

            plan_dict = plan.model_dump(mode="json")

            await self._log_interaction(
                user_id=user_id,
                workspace_id=workspace_id,
                trace_id=trace.trace_id,
                message_preview=message[:500],
                intent=intent,
                plan=plan,
                conversation_id=conversation_id,
            )

            # Emit plan event (replaces old "decision" event)
            yield {
                "event": "plan",
                "plan": plan_dict,
                "run_id": None,
            }

            _fire_event(
                "plan_created",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=None,
                payload={
                    "goal": plan.goal,
                    "trace_id": trace.trace_id,
                },
            )

            # Step 2: Pre-resolve routing and tools
            step_routing: list[tuple[PlanStep, str, list[dict]]] = []
            user_steps: list[PlanStep] = []

            async with self._db_factory() as db:
                resolver = CapabilityResolver(db, workspace_id)
                for step in plan.steps:
                    if step.actor == "user":
                        user_steps.append(step)
                        continue
                    if step.capability.startswith("system."):
                        step_routing.append((step, "", []))
                    elif step.capability in ("reason", "respond"):
                        step_routing.append((step, "presenter", []))
                    elif step.capability == "perceive":
                        # Broad read: Perceiver gets ALL its tools, decides
                        # autonomously which sources to query.
                        step_routing.append((step, "perceiver", []))
                    else:
                        agent_name = await route_step(step.capability, resolver)
                        tools = await resolver.resolve_for_step(step.capability)
                        step_routing.append((step, agent_name, tools))

            # Step 3: Execute steps with streaming
            presenter_text = ""
            step_outputs: dict[str, str] = {}
            for step, agent_name, tools in step_routing:
                if step.capability.startswith("system."):
                    await self._handle_system_capability(step, plan, user_id, workspace_id)
                    continue

                if not agent_name:
                    error_msg = f"No tools available for capability '{step.capability}'"
                    logger.warning(error_msg)
                    yield {"event": "step_error", "step_id": step.step_id, "error": error_msg}
                    continue

                # Plan mode: skip risky execution, present the plan
                if mode == "plan" and step.risk in ("medium", "high"):
                    yield {
                        "event": "plan_ready",
                        "plan_id": plan.plan_id,
                        "message": ("Plan created. Review and approve to execute."),
                    }
                    continue

                agent_message = (
                    f"Execute this step: {step.description}\n"
                    f"Goal: {plan.goal}\n"
                    f"User message: {message}"
                )
                # Inject prior step results so downstream agents see earlier outputs
                if step_outputs:
                    parts = []
                    for key, output in step_outputs.items():
                        parts.append(f"[{key} output]:\n{str(output)[:3000]}")
                    agent_message += (
                        "\n\n--- Prior step results ---\n"
                        + "\n\n".join(parts)
                        + "\n--- End of prior step results ---\n"
                    )
                if history_block:
                    agent_message = f"{history_block}\n\n{agent_message}"

                async for evt in self._call_agent_stream(
                    agent_name,
                    message=agent_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                    tools_override=tools if tools else None,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        done_text = evt.get("text", "")
                        # Capture all step outputs for downstream agents
                        if done_text:
                            step_outputs[agent_name] = done_text
                        # Capture text from respond/reason steps for surface preview
                        if step.capability in ("reason", "respond"):
                            presenter_text = done_text

            # Build user action block from user_steps
            user_action_block = ""
            if user_steps:
                actions = "\n".join(
                    f"- {s.description}" + (f" ({s.user_context})" if s.user_context else "")
                    for s in user_steps
                )
                user_action_block = f"\n\nUser actions required:\n{actions}"
                yield {
                    "event": "user_actions",
                    "steps": [
                        {"description": s.description, "context": s.user_context}
                        for s in user_steps
                    ],
                }

            # Step 4: Presenter formatting — always call the Presenter so
            # the user receives a conversational response.  system.respond
            # steps are no-ops in _handle_system_capability, and
            # reason/respond steps execute with wrong context, so relying on
            # them to produce the user-facing answer leaves the chat empty.
            if True:
                # Collect prior step results so Presenter can reference them
                prior_results_block = ""
                if step_outputs:
                    parts = []
                    for agent_key, output in step_outputs.items():
                        truncated = str(output)[:3000]
                        parts.append(f"[{agent_key} output]:\n{truncated}")
                    prior_results_block = (
                        "\n\n--- Prior step results (use these to answer the user) ---\n"
                        + "\n\n".join(parts)
                        + "\n--- End of prior step results ---\n"
                    )

                presenter_msg = (
                    f"Respond to the user ({surface}). "
                    f"Be conversational and helpful.\n\n"
                    f"User message: {message}\n"
                    f"Intent: {intent}\n"
                )
                if prior_results_block:
                    presenter_msg += prior_results_block
                if plan_text:
                    presenter_msg += (
                        f"Plan: {json.dumps(plan_dict)}\nAnalysis: {plan_text[:2000]}\n"
                    )
                if user_action_block:
                    presenter_msg += user_action_block
                if history_block:
                    presenter_msg = f"{history_block}\n\n{presenter_msg}"

                async for evt in self._call_agent_stream(
                    "presenter",
                    message=presenter_msg,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        presenter_text = evt.get("text", "")
                        yield {
                            "event": "response",
                            "text": presenter_text,
                        }

            _fire_event(
                "run_completed",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=None,
                payload={"trace_id": trace.trace_id},
            )

            # Push workspace surface (Presenter-driven)
            surface_id = None
            try:
                surface_spec = extract_surface_spec(presenter_text)
                if surface_spec and surface_spec.should_surface:
                    surface_id = await self._push_presenter_surface(
                        spec=surface_spec,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        run_id=None,
                        response_text=presenter_text,
                    )
            except Exception:
                logger.warning("Surface push failed", exc_info=True)

            # Interaction learning (async, non-blocking)
            if self._interaction_learner:
                await self._ensure_learner_redis()
                self._spawn_background(
                    self._interaction_learner.learn(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        user_message=message,
                        agent_response=presenter_text,
                        intent=intent,
                        trace_id=trace.trace_id,
                    )
                )

            yield {
                "event": "done",
                "trace_id": trace.trace_id,
                "run_id": None,
                **({"surface_id": surface_id} if surface_id else {}),
            }

        except Exception as e:
            logger.error("process_message_stream failed: %s", e, exc_info=True)
            _fire_event(
                "run_failed",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=None,
                payload={"error": str(e)[:200]},
            )
            yield {"event": "error", "message": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )

    async def _call_agent_stream(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
        capability_summary: str = "",
        tools_override: list[dict] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Call a sub-agent with streaming, yielding SSE-compatible dicts."""
        agent = self._agents.get(agent_name)
        if not agent:
            yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
            return

        model = self._get_model_for_agent(agent)

        if tools_override is not None:
            tools = self._apply_cache_control_to_tools(tools_override)
        else:
            tools = self._apply_cache_control_to_tools(
                await self._get_tools_for_agent(agent, workspace_id=workspace_id)
            )

        # Auto-generate capability summary for planner if not provided
        if agent_name == "planner" and not capability_summary:
            try:
                from src.orchestrator.capability_summary import (
                    generate_capability_summary,
                )

                async with self._db_factory() as db:
                    capability_summary = await generate_capability_summary(db, workspace_id)
            except Exception:
                logger.debug("Failed to generate capability summary", exc_info=True)

        context_block = await self._assemble_context(
            agent_name, message, user_id=user_id, workspace_id=workspace_id
        )
        system_blocks = self._build_system_prompt(
            agent, context_block, capability_summary=capability_summary
        )

        async for evt in agent_loop(
            client=self._client,
            agent=agent,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=user_id,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            services=self._services,
            budget=self._budget,
            trace=trace,
            execute_tool_fn=self._execute_tool,
            max_tool_rounds=max_tool_rounds,
            stream=True,
            circuit_breaker=self._circuit_breaker,
        ):
            if isinstance(evt, LoopAgentStart):
                yield {"event": "agent_start", "agent": evt.agent, "model": evt.model}
            elif isinstance(evt, LoopThinking):
                yield {
                    "event": "thinking",
                    "agent": evt.agent,
                    "text": evt.text,
                    "is_thinking": evt.is_thinking,
                }
            elif isinstance(evt, LoopTextDelta):
                yield {"event": "text_delta", "agent": evt.agent, "text": evt.text}
            elif isinstance(evt, LoopToolCall):
                yield {
                    "event": "tool_call",
                    "agent": evt.agent,
                    "tool": evt.tool_name,
                    "input": evt.tool_input,
                }
            elif isinstance(evt, LoopToolResult):
                yield {
                    "event": "tool_result",
                    "agent": evt.agent,
                    "tool": evt.tool_name,
                    "result": evt.result,
                    "blocked": evt.blocked,
                    "latency_ms": evt.latency_ms,
                }
            elif isinstance(evt, LoopError):
                yield {"event": "error", "agent": evt.agent, "message": evt.message}
            elif isinstance(evt, LoopDone):
                yield {
                    "event": "agent_done",
                    "agent": evt.agent,
                    "text": evt.text,
                    "input_tokens": evt.input_tokens,
                    "output_tokens": evt.output_tokens,
                    "cache_creation_tokens": evt.cache_creation_tokens,
                    "cache_read_tokens": evt.cache_read_tokens,
                    "tools_called": evt.tools_called,
                    "latency_ms": evt.latency_ms,
                    "cost_usd": round(evt.cost_usd, 6),
                }

    async def run_cross_source_synthesis(
        self,
        source_names: list[str],
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Internal cross-source synthesis — no user-facing artifacts.

        Called by the scheduler when 2+ perception sources have new events
        in the same tick.  Asks the Planner to find cross-cutting insights
        and queues any resulting plans for background execution.

        Unlike process_message(), this does NOT create a lightweight run,
        Presenter formatting, or A2UI surface push.
        """
        trace = self._trace_manager.start_trace("cross_source_synthesis")
        try:
            planner_result = await self._call_agent(
                "planner",
                message=(
                    f"Synthesize recent observations across these sources: "
                    f"{', '.join(source_names)}. "
                    f"Identify cross-cutting insights, connections between "
                    f"events, or actions that span multiple sources."
                ),
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )
            # Queue any actionable plans from the synthesis
            plan = await self._queue_perception_plan(
                planner_result,
                "synthesis",
                user_id,
                workspace_id,
                trace.trace_id,
            )
            return {
                "status": "completed",
                "plan_goal": plan.goal if plan else None,
            }
        except Exception as e:
            logger.warning("Cross-source synthesis failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def run_perception_cycle(self, source: str, user_id: str, workspace_id: str = "") -> dict:
        """Run a perception cycle for a specific data source.

        Step 1: Poll the connector directly (no Claude call — just API fetch).
        Step 2: If new events found, Librarian extracts entities/memories.
        Step 3: Planner evaluates importance and creates plans if needed.
        Step 4: Apply perception policy from planner response.
        Step 5: Extract decision and queue execution if actionable.
        """
        trace = self._trace_manager.start_trace(f"perception_{source}")

        try:
            # Check budget (only for Librarian + Planner calls, polling is cheap)
            async with self._db_factory() as db:
                budget_status = await self._budget.get_budget_status(db)
            if not self._budget.should_allow_perception(budget_status):
                logger.warning(
                    "perception_skipped_budget",
                    extra={"source": source, "mode": budget_status.budget_mode},
                )
                return {"status": "skipped", "reason": "budget_exhausted"}

            # Step 1: Poll the connector directly for new events
            raw_events, new_cursor, poll_error, cursor_type = await self._poll_connector(
                source, user_id, workspace_id
            )

            if poll_error:
                logger.warning(
                    "perception_poll_failed",
                    extra={"source": source, "error": poll_error},
                )
                return {"status": "error", "source": source, "error": poll_error}

            if not raw_events:
                logger.info(
                    "perception_no_new_events",
                    extra={"source": source},
                )
                # Save cursor even on empty polls so incremental sync
                # advances (e.g. Gmail historyId, Calendar syncToken).
                await self._update_cursor(source, user_id, workspace_id, new_cursor, cursor_type)
                return {"status": "completed", "source": source, "events": 0}

            # Ingest raw events into normalized_events table
            event_summaries = await self._ingest_raw_events(raw_events, user_id, workspace_id)

            # Update the observation cursor immediately after ingestion so that
            # downstream failures (Librarian, Planner) don't cause re-polling
            # the same events on the next cycle.
            await self._update_cursor(source, user_id, workspace_id, new_cursor, cursor_type)

            # Fetch full thread context for reply emails
            thread_contexts = await _fetch_thread_contexts(raw_events, user_id, workspace_id)

            observer_summary = f"Polled {source}: {len(raw_events)} new event(s).\n" + "\n".join(
                f"- {s}" for s in event_summaries[:20]
            )
            if thread_contexts:
                observer_summary += "\n\n--- Thread Context (full conversation) ---"
                for tid, ctx in thread_contexts.items():
                    messages = ctx.get("messages", [])
                    if messages:
                        observer_summary += f"\nThread {tid} ({len(messages)} messages):"
                        for msg in messages[-5:]:
                            snippet = msg.get("snippet", msg.get("body", ""))[:200]
                            sender = msg.get("from", "unknown")
                            observer_summary += f"\n  [{sender}]: {snippet}"

            # Step 2: Librarian extracts entities and memories
            librarian_result = await self._call_agent(
                "librarian",
                message=f"Process these observations from {source} and extract "
                f"entities and memories:\n{observer_summary}",
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Enrich with correlation context for thread-aware planning
            correlation_context = ""
            if event_summaries:
                try:
                    from src.services.event_correlator import EventCorrelator

                    async with self._db_factory() as db:
                        correlator = EventCorrelator(db)
                        seen_entities: set[str] = set()
                        max_entities = 5
                        for raw_evt in raw_events:
                            if len(seen_entities) >= max_entities:
                                break
                            eid = getattr(raw_evt, "entity_id", None)
                            if eid and eid not in seen_entities:
                                seen_entities.add(eid)
                                thread = await correlator.detect_thread(
                                    user_id, eid, workspace_id=workspace_id
                                )
                                if thread and thread["event_count"] > 1:
                                    correlation_context += (
                                        f"\n[Thread detected] entity={thread['entity_id']} "
                                        f"has {thread['event_count']} events "
                                        f"(first: {thread['first_at']}, "
                                        f"last: {thread['last_at']})"
                                    )
                except Exception:
                    logger.warning("Correlation enrichment failed", exc_info=True)

            # Step 2b: Assess relevance of signals against user context
            try:
                from src.services.memory_service import MemoryService
                from src.services.relevance_assessor import (
                    PerceptionSignal,
                    UserContext,
                    assess_relevance,
                )

                signal = PerceptionSignal(
                    source=source,
                    event_type=f"perception_{source}",
                    summary=observer_summary[:500],
                )

                # Build user context from goals + preferences
                user_goals = []
                user_prefs = []
                try:
                    async with self._db_factory() as db:
                        mem_svc = MemoryService(self._settings, db)
                        # get_user_preferences(user_id, category, max_results, workspace_id)
                        prefs = await mem_svc.get_user_preferences(
                            user_id, workspace_id=workspace_id
                        )
                        for p in prefs[:10]:
                            if getattr(p, "memory_type", "") == "goal":
                                user_goals.append(p.fact_text)
                            else:
                                user_prefs.append(p.fact_text)
                except Exception:
                    logger.debug("Failed to load user context for relevance", exc_info=True)

                user_context = UserContext(
                    goals=user_goals,
                    preferences=user_prefs,
                )

                # Fetch engagement context for the assessor
                engagement_context = ""
                try:
                    from src.services.engagement_service import EngagementService

                    async with self._db_factory() as db:
                        eng_svc = EngagementService(db, workspace_id)
                        if await eng_svc.is_suppressed(signal.source, signal.event_type):
                            logger.debug(
                                "Signal suppressed: %s/%s",
                                signal.source,
                                signal.event_type,
                            )
                            return {"status": "suppressed", "source": source}
                        engagement_context = await eng_svc.get_engagement_context()
                except Exception:
                    logger.debug("Failed to load engagement context", exc_info=True)

                assessment = await assess_relevance(
                    signal,
                    user_context,
                    self._client,
                    engagement_context=engagement_context,
                )

                # Route by notification tier
                if assessment.notification_tier == "briefing":
                    try:
                        async with self._db_factory() as db:
                            mem_svc = MemoryService(self._settings, db)
                            await mem_svc.store_briefing_memory(
                                user_id=user_id,
                                workspace_id=workspace_id,
                                text=f"{observer_summary[:300]}\n\nWhy: {assessment.reasoning}",
                                source=f"perception:{source}",
                                relevance_score=assessment.relevance_score,
                                signal_source=source,
                            )
                            await db.commit()
                    except Exception:
                        logger.warning("Failed to store briefing memory", exc_info=True)

                elif assessment.notification_tier == "push":
                    try:
                        await self._push_insight_surface(signal, assessment, user_id, workspace_id)
                    except Exception:
                        logger.warning(
                            "Failed to push insight surface for signal",
                            exc_info=True,
                        )

                else:
                    # silent tier: in world model from Librarian, record as ignored
                    try:
                        async with self._db_factory() as db:
                            from src.services.engagement_service import EngagementService

                            eng_svc = EngagementService(db, workspace_id)
                            await eng_svc.record_engagement(
                                signal.source, signal.event_type, "ignored"
                            )
                            await db.commit()
                    except Exception:
                        logger.debug("Failed to record silent tier engagement", exc_info=True)

            except Exception:
                logger.warning("Relevance assessment failed, continuing without", exc_info=True)

            # Step 3: Planner evaluates if any action is needed
            planner_message = (
                f"Evaluate these observations from {source}. "
                f"Create plans for anything important.\n"
                f"Optionally include a perception_policy JSON block to control "
                f"how soon {source} should next be checked:\n{observer_summary}"
            )
            if correlation_context:
                planner_message += f"\n\n--- Correlation Context ---{correlation_context}"

            planner_result = await self._call_agent(
                "planner",
                message=planner_message,
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Step 4: Extract and apply perception policy if present
            await self._apply_perception_policy_from_planner(
                planner_result, source, user_id, workspace_id, len(raw_events)
            )

            # Step 5: Extract plan and queue execution if actionable
            perception_plan = await self._queue_perception_plan(
                planner_result,
                source,
                user_id,
                workspace_id,
                trace.trace_id,
            )

            # Publish perception completed event
            await self._publish_event(
                "perception_completed",
                user_id,
                {
                    "source": source,
                    "trace_id": trace.trace_id,
                    "event_count": len(raw_events),
                    "plan_goal": perception_plan.goal if perception_plan else None,
                },
                trace_id=trace.trace_id,
            )

            return {
                "status": "completed",
                "source": source,
                "trace_id": trace.trace_id,
                "events": len(raw_events),
                "librarian": librarian_result,
                "planner": planner_result,
                "plan_goal": perception_plan.goal if perception_plan else None,
                "plan_id": perception_plan.plan_id if perception_plan else None,
            }

        except Exception as e:
            logger.error("perception_cycle failed: %s", e, exc_info=True)
            # DLQ: capture cycle-level failures for inspection/retry
            try:
                from src.services.dead_letter import DeadLetterService

                async with self._db_factory() as db:
                    dlq = DeadLetterService(db)
                    await dlq.enqueue(
                        user_id=user_id,
                        operation_type="perception_cycle",
                        error_type=type(e).__name__,
                        error_message=str(e),
                        source_id=f"perception:{source}",
                        payload={
                            "source": source,
                            "trace_id": trace.trace_id,
                            "workspace_id": workspace_id,
                        },
                        workspace_id=workspace_id,
                    )
                    await db.commit()
            except Exception:
                logger.debug("DLQ enqueue failed for perception %s", source, exc_info=True)
            return {"status": "error", "source": source, "error": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def _poll_connector(
        self, source: str, user_id: str, workspace_id: str
    ) -> tuple[list, str | None, str | None, str]:
        """Poll a connector for new events. Returns (events, new_cursor, error, cursor_type)."""
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.services.oauth_manager import OAuthManager

        connector_cls = CONNECTOR_REGISTRY.get(source)
        if not connector_cls:
            return [], None, f"No connector registered for source: {source}", "opaque"

        connector = connector_cls(settings=self._settings)
        cursor_type = connector.cursor_type

        # Get OAuth credentials
        oauth_mgr = OAuthManager(
            self._db_factory,
            encryption_key=self._settings.oauth_encryption_key,
            settings=self._settings,
        )
        # Map source to OAuth provider (gmail/calendar share "google" provider)
        oauth_provider = "google" if source in ("gmail", "calendar") else source
        access_token = await oauth_mgr.get_valid_token(user_id, oauth_provider)
        if not access_token:
            return (
                [],
                None,
                f"No valid credentials for {source} — user may need to re-authorize",
                cursor_type,
            )

        # Get current cursor
        cursor = None
        async with self._db_factory() as db:
            from sqlalchemy import select

            from src.models.observation_cursor import ObservationCursor

            result = await db.execute(
                select(ObservationCursor.cursor_value).where(
                    ObservationCursor.user_id == user_id,
                    ObservationCursor.source == source,
                )
            )
            row = result.first()
            if row:
                cursor = row[0]

        try:
            events, new_cursor = await asyncio.wait_for(
                connector.poll(user_id, cursor, {"access_token": access_token}),
                timeout=30,
            )
            return events, new_cursor, None, cursor_type
        except asyncio.TimeoutError:
            logger.warning(
                "Connector %s poll timed out after 30s for user %s",
                source,
                user_id,
            )
            return [], cursor, "Poll timed out after 30s", cursor_type
        except Exception as e:
            from src.integrations.mcp_errors import classify_error

            error_code = classify_error(e)
            logger.warning(
                "connector_poll_error",
                extra={"source": source, "error_code": error_code, "error": str(e)[:500]},
            )
            return [], None, f"Poll failed for {source} ({error_code}): {e}", cursor_type

    async def _ingest_raw_events(
        self, raw_events: list, user_id: str, workspace_id: str
    ) -> list[str]:
        """Ingest raw events into the event processor. Returns summary strings."""
        summaries = []
        async with self._db_factory() as db:
            from src.services.dead_letter import DeadLetterService
            from src.services.event_processor import EventProcessor

            event_bus = await self._ensure_event_bus()
            dead_letter = DeadLetterService(db)

            processor = EventProcessor(
                self._settings,
                db,
                world_model=self._services.world_model,
                memory_service=self._services.memory_service,
                dead_letter=dead_letter,
                event_bus=event_bus,
                notifier=self._services.notifier,
                embedding_service=self._services.extras.get("embedding_service"),
                vector_store=self._services.vector_store,
            )
            for raw in raw_events:
                try:
                    event_id = await processor.process(
                        raw,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                    title = raw.title or getattr(raw, "raw_data", {}).get("subject", "")
                    summary = f"[{raw.source}] {raw.event_type}: {title}"
                    if event_id:
                        summary += f" (event_id={event_id})"
                    summaries.append(summary)
                except Exception as e:
                    await db.rollback()
                    logger.warning(
                        "event_ingest_failed",
                        extra={
                            "source": raw.source,
                            "event_type": raw.event_type,
                            "error": str(e)[:500],
                        },
                    )
                    summaries.append(f"[{raw.source}] {raw.event_type} (ingest error)")
                    try:
                        await dead_letter.enqueue(
                            user_id=user_id,
                            operation_type="event_ingest",
                            error_type=type(e).__name__,
                            error_message=str(e),
                            source_id=raw.entity_id,
                            payload={
                                "source": raw.source,
                                "event_type": raw.event_type,
                                "entity_id": raw.entity_id,
                            },
                            workspace_id=workspace_id,
                        )
                    except Exception:
                        logger.debug("DLQ enqueue failed", exc_info=True)
            await db.commit()
        return summaries

    async def _update_cursor(
        self,
        source: str,
        user_id: str,
        workspace_id: str,
        new_cursor: str | None,
        cursor_type: str = "opaque",
    ) -> None:
        """Update the observation cursor after a successful poll."""
        if not new_cursor:
            return
        async with self._db_factory() as db:
            from datetime import datetime, timezone

            from sqlalchemy import select

            from src.models.observation_cursor import ObservationCursor

            result = await db.execute(
                select(ObservationCursor).where(
                    ObservationCursor.user_id == user_id,
                    ObservationCursor.source == source,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.cursor_value = new_cursor
                existing.last_observation_at = datetime.now(timezone.utc)
            else:
                from ulid import ULID

                db.add(
                    ObservationCursor(
                        cursor_id=f"cur_{ULID()}",
                        user_id=user_id,
                        workspace_id=workspace_id,
                        source=source,
                        cursor_type=cursor_type,
                        cursor_value=new_cursor,
                        last_observation_at=datetime.now(timezone.utc),
                    )
                )
            await db.commit()

    async def generate_briefing(self, user_id: str, workspace_id: str = "") -> dict:
        """Generate the daily briefing through the Presenter agent.

        Uses the get_briefing tool to fetch real data from the intelligence
        backend (events, plans, approvals, goals) and then formats it through
        the Presenter agent for user-facing delivery.
        """
        trace = self._trace_manager.start_trace("scheduled_briefing")
        try:
            # Step 1: Gather raw briefing data from intelligence server
            raw_data = await self._execute_tool(
                "get_briefing", {"date": "today"}, user_id=user_id, workspace_id=workspace_id
            )

            # Step 2: Let Presenter format it into a user-friendly briefing
            result = await self._call_agent(
                "presenter",
                message=(
                    "Format the following briefing data into a clear, concise daily briefing "
                    "for the user. Include: top priorities, recent changes, pending approvals, "
                    "and recommended next actions.\n\n"
                    f"Raw briefing data:\n{json.dumps(raw_data, indent=2, default=str)}"
                ),
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            await self._publish_event(
                "briefing_generated",
                user_id,
                {"trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            # B3: Deliver briefing to user via notifications + workspace surface
            try:
                if self._services.notifier:
                    await self._services.notifier.notify(
                        user_id=user_id,
                        notification_type="briefing",
                        title="Daily Briefing",
                        body=str(result)[:500],
                        workspace_id=workspace_id,
                    )
                await self._push_workspace_surface(
                    PlanOutput(
                        goal="Daily Briefing",
                        reasoning=str(result)[:200],
                        steps=[
                            PlanStep(
                                description="Briefing update",
                                capability="system.add_to_brief",
                            )
                        ],
                    ),
                    user_id=user_id,
                    workspace_id=workspace_id,
                    response_text=str(result)[:1000],
                )
            except Exception:
                logger.debug("Briefing delivery failed", exc_info=True)

            return {"status": "completed", "trace_id": trace.trace_id, "briefing": result}
        except Exception as e:
            logger.error("generate_briefing failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def _ensure_event_bus(self):
        """Lazily initialize the event bus. Returns the bus or None on failure.

        Uses asyncio.Lock to prevent race condition where two concurrent
        requests both create a Redis connection (C5).
        """
        if self._event_bus is not None:
            return self._event_bus
        async with self._event_bus_lock:
            # Double-check after acquiring lock
            if self._event_bus is not None:
                return self._event_bus
            try:
                import redis.asyncio as aioredis

                from src.services.event_bus import EventBus

                self._event_bus_redis = aioredis.from_url(
                    self._settings.redis_url, decode_responses=True
                )
                self._event_bus = EventBus(self._event_bus_redis)
            except Exception:
                logger.debug("Failed to init event_bus", exc_info=True)
        return self._event_bus

    async def _publish_event(
        self, event_type: str, user_id: str, payload: dict, trace_id: str | None = None
    ) -> None:
        """Publish an agent action event to the event bus (best-effort)."""
        try:
            event_bus = await self._ensure_event_bus()
            if event_bus is None:
                return

            stream = event_bus.agent_stream(user_id)
            metadata = {"trace_id": trace_id} if trace_id else {}
            await event_bus.publish(stream, event_type, payload, user_id, metadata)
        except Exception:
            logger.debug("Failed to publish event %s to bus", event_type, exc_info=True)

    async def _emit_runtime_event(
        self,
        event_type: str,
        *,
        workspace_id: str,
        user_id: str,
        run_id: str | None = None,
        step_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Emit a durable runtime event to DB + Redis (best-effort)."""
        try:
            async with self._db_factory() as db:
                from src.services.runtime_events import RuntimeEventEmitter

                emitter = RuntimeEventEmitter(db, workspace_id, self._event_bus)
                await emitter.emit(
                    event_type,
                    run_id=run_id,
                    step_id=step_id,
                    user_id=user_id,
                    payload=payload,
                )
                await db.commit()
        except Exception:
            logger.warning("Failed to emit runtime event %s", event_type, exc_info=True)

    async def _check_surface_rate(self, user_id: str, surface_type: str) -> bool:
        """Return True if push is allowed under rate limit.

        Uses Redis INCR with TTL for a sliding window counter.
        Workspace: 5 per minute. Insight: 3 per 30 minutes.
        """
        event_bus = await self._ensure_event_bus()
        if not event_bus or not getattr(event_bus, "_redis", None):
            return True

        redis = event_bus._redis
        if surface_type == "insight":
            key = f"jarvis:surface_rate:insight:{user_id}"
            limit, window = 3, 1800
        else:
            key = f"jarvis:surface_rate:workspace:{user_id}"
            limit, window = 5, 60

        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
        return count <= limit

    async def _push_presenter_surface(
        self,
        spec,
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> str | None:
        """Push a Presenter-specified surface to the workspace.

        Builds WorkspaceSurfacePush from a SurfaceSpec produced by the Presenter agent.
        """
        from datetime import datetime, timedelta, timezone

        from ulid import ULID

        from src.orchestrator.contracts import WorkspaceSurfacePush
        from src.services.surface_mapping import extract_surface_data
        from src.ui.contracts import SurfaceMetric, SurfacePreview
        from src.ui.renderer import build_detail_config

        if not await self._check_surface_rate(user_id, "workspace"):
            logger.debug("Presenter surface rate-limited for user %s", user_id)
            return None

        try:
            event_bus = await self._ensure_event_bus()
            if not event_bus:
                return None

            surface_id = f"surf_{ULID()}"
            preview = SurfacePreview(
                title=spec.title,
                subtitle=spec.subtitle,
                status=spec.status,
                priority=spec.priority,
                metrics=[SurfaceMetric(**m) for m in spec.metrics] if spec.metrics else [],
                tags=spec.tags or [],
            )
            detail_config = build_detail_config(spec.kind, surface_id)

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind=spec.kind,
                preview=preview.model_dump(mode="json"),
                detail_config=(detail_config.model_dump(mode="json") if detail_config else None),
                source_run_id=run_id,
                response_preview=(response_text[:300] if response_text else None),
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps({"type": "surface", "surface": surface.model_dump(mode="json")})
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to DB
            try:
                from src.models.ui_state import UISurface

                surface_data = extract_surface_data(response_text)

                async with self._db_factory() as db:
                    payload = surface.model_dump(mode="json")
                    if surface_data:
                        payload["surface_data"] = surface_data

                    db.add(
                        UISurface(
                            surface_id=surface.id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type=spec.kind,
                            payload=payload,
                            preview=preview.model_dump(mode="json"),
                            detail_config=(
                                detail_config.model_dump(mode="json") if detail_config else None
                            ),
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to persist presenter surface", exc_info=True)

            return surface_id
        except Exception:
            logger.warning("Failed to push presenter surface", exc_info=True)
            return None

    async def _push_workspace_surface(
        self,
        plan: "PlanOutput",
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> str | None:
        """Push a typed surface to the workspace via Redis Pub/Sub.

        Derives surface kind from plan step capabilities.
        Only pushes for plans with visual value beyond the chat response.
        Returns the generated surface_id on success, None otherwise.
        """
        from datetime import datetime, timedelta, timezone

        from src.orchestrator.contracts import WorkspaceSurfacePush
        from src.ui.renderer import build_detail_config

        mapping = derive_surface_kind(plan)
        if not mapping:
            return None

        if not await self._check_surface_rate(user_id, "workspace"):
            logger.debug("Surface push rate-limited for user %s", user_id)
            return None

        kind, default_title = mapping

        try:
            event_bus = await self._ensure_event_bus()
            if not event_bus:
                return

            from ulid import ULID

            surface_id = f"surf_{ULID()}"
            preview = build_surface_preview_from_plan(plan, kind, default_title, response_text)
            detail_config = build_detail_config(kind, surface_id)

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind=kind,
                preview=preview.model_dump(mode="json"),
                detail_config=(detail_config.model_dump(mode="json") if detail_config else None),
                decision=None,
                source_run_id=run_id,
                response_preview=(response_text[:300] if response_text else None),
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps(
                {
                    "type": "surface",
                    "surface": surface.model_dump(mode="json"),
                }
            )
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to ui_surfaces table so the workspace survives page refresh
            try:
                from src.models.ui_state import UISurface

                async with self._db_factory() as db:
                    db.add(
                        UISurface(
                            surface_id=surface.id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type=kind,
                            payload=surface.model_dump(mode="json"),
                            preview=preview.model_dump(mode="json"),
                            detail_config=(
                                detail_config.model_dump(mode="json") if detail_config else None
                            ),
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug(
                    "Failed to persist workspace surface to DB",
                    exc_info=True,
                )
            return surface_id
        except Exception:
            logger.warning("Failed to push workspace surface", exc_info=True)
            return None

    async def _push_insight_surface(
        self,
        signal: "PerceptionSignal",
        assessment: "RelevanceAssessment",
        user_id: str,
        workspace_id: str,
    ) -> None:
        """Push a proactive insight surface to the workspace.

        Called when the relevance assessor routes a signal to the push tier.
        Creates a WorkspaceSurfacePush with kind='proactive_insight' and
        persists to ui_surfaces for workspace reconnection.
        """
        from datetime import datetime, timedelta, timezone

        from ulid import ULID

        from src.orchestrator.contracts import (
            InsightSurfaceData,
            SuggestedActionRef,
            WorkspaceSurfacePush,
        )
        from src.ui.contracts import SurfacePreview

        try:
            event_bus = await self._ensure_event_bus()
            if not event_bus:
                return

            if not await self._check_surface_rate(user_id, "insight"):
                logger.debug("Insight surface rate-limited for user %s", user_id)
                return

            surface_id = f"surf_{ULID()}"

            suggested_actions = [
                SuggestedActionRef(
                    description=a.description,
                    capability=a.capability,
                    action_input=a.action_input,
                    action_preview=_build_action_preview(a.capability, a.description),
                )
                for a in assessment.suggested_actions
            ]

            insight_data = InsightSurfaceData(
                signal_source=signal.source,
                signal_category=signal.event_type,
                signal_summary=signal.summary,
                relevance_score=assessment.relevance_score,
                relevance_reasoning=assessment.reasoning,
                related_goals=assessment.relates_to_goals,
                suggested_actions=suggested_actions,
            )

            preview = SurfacePreview(
                title=signal.summary[:120],
                subtitle=assessment.reasoning[:200] if assessment.reasoning else None,
                status="proposal",
                priority="high" if assessment.urgency == "immediate" else "medium",
                tags=[signal.source],
            )

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind="proactive_insight",
                preview=preview.model_dump(mode="json"),
                detail_config=None,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            # Include insight data in the payload for the frontend
            surface_payload = surface.model_dump(mode="json")
            surface_payload["insight_data"] = insight_data.model_dump(mode="json")

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps({"type": "surface", "surface": surface_payload})
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to ui_surfaces
            try:
                from src.models.ui_state import UISurface

                async with self._db_factory() as db:
                    db.add(
                        UISurface(
                            surface_id=surface_id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type="proactive_insight",
                            payload=surface_payload,
                            preview=preview.model_dump(mode="json"),
                            detail_config=None,
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to persist insight surface to DB", exc_info=True)

        except Exception:
            logger.warning("Failed to push insight surface", exc_info=True)

    async def _load_conversation_history(
        self,
        conversation_id: str | None,
        max_messages: int = 20,
        max_chars: int = 8000,
        user_id: str = "",
    ) -> str:
        """Load recent conversation history from DB for multi-turn context.

        Returns a formatted block of prior messages or empty string.
        Truncates to stay within token budget.
        """
        if not conversation_id or not self._db_factory:
            return ""

        try:
            from sqlalchemy import select

            from src.models.conversations import Message

            async with self._db_factory() as db:
                result = await db.execute(
                    select(Message.role, Message.content, Message.metadata_)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(max_messages + 1)  # +1 for the just-saved user message
                )
                rows = result.all()

            if len(rows) <= 1:
                # Only the current message — no history
                return ""

            # Reverse to chronological, skip the last (current) user message
            history = list(reversed(rows[1:]))

            lines: list[str] = []
            total = 0
            for role, content, meta in history:
                label = "User" if role == "user" else "Assistant"
                snippet = content[:1000] if len(content) > 1000 else content
                # B4: Annotate with decision type for execution context
                decision_tag = ""
                if meta and isinstance(meta, dict):
                    decision_data = meta.get("decision")
                    if isinstance(decision_data, dict):
                        decision_tag = f" [{decision_data.get('decision', '')}]"
                line = f"{label}{decision_tag}: {snippet}"
                lines.append(line)
                total += len(line)

            if not lines:
                return ""

            # If history exceeds budget, summarize older messages
            if total > max_chars and len(lines) > 5:
                recent = lines[-5:]
                older = lines[:-5]
                summary = await self._summarize_history(
                    older, conversation_id=conversation_id, user_id=user_id
                )
                lines = [f"[Earlier conversation summary]: {summary}"] + recent

            # Final trim to budget
            output_lines: list[str] = []
            remaining = max_chars
            for line in lines:
                if remaining - len(line) < 0:
                    break
                output_lines.append(line)
                remaining -= len(line)

            if not output_lines:
                return ""

            return (
                "--- CONVERSATION HISTORY (most recent messages) ---\n"
                + "\n".join(output_lines)
                + "\n--- END HISTORY ---"
            )
        except Exception:
            logger.debug("Failed to load conversation history", exc_info=True)
            return ""

    async def _summarize_history(
        self, lines: list[str], conversation_id: str | None = None, user_id: str = ""
    ) -> str:
        """Summarize older conversation messages using Haiku (cheap, fast)."""
        try:
            if self._settings.use_bedrock:
                model = BEDROCK_MODEL_TIERS["haiku"]
            else:
                model = MODEL_TIERS["haiku"]

            text = "\n".join(lines)[:4000]
            response = await self._client.messages.create(
                model=model,
                max_tokens=300,
                temperature=0,
                system=[
                    {
                        "type": "text",
                        "text": (
                            "Summarize this conversation in 2-3 sentences. "
                            "Focus on: topics discussed, decisions made, "
                            "and any pending items."
                        ),
                    }
                ],
                messages=[{"role": "user", "content": text}],
            )
            summary = "".join(b.text for b in response.content if b.type == "text")

            # Embed conversation summary into Qdrant for semantic search
            if summary and conversation_id:
                try:
                    from datetime import datetime, timezone

                    from src.services.embedding_service import EmbeddingService
                    from src.services.vector_store import VectorStore

                    if self._settings.qdrant_url:
                        vs = VectorStore(self._settings)
                        es = EmbeddingService(self._settings)
                        embedding = await es.embed_text(summary)
                        if embedding:
                            await vs.upsert(
                                collection="conversations",
                                id=conversation_id,
                                vector=embedding,
                                payload={
                                    "conversation_id": conversation_id,
                                    "workspace_id": "",
                                    "message_count": len(lines),
                                    "summary": summary[:500],
                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                },
                                user_id=user_id,
                            )
                except Exception:
                    logger.debug(
                        "Conversation embedding failed for %s",
                        conversation_id,
                        exc_info=True,
                    )

            return summary
        except Exception:
            logger.debug("History summarization failed", exc_info=True)
            # Fallback: just truncate
            return "\n".join(lines)[:500] + "..."

    async def _assemble_context(
        self, agent_name: str, message: str, user_id: str, workspace_id: str = ""
    ) -> str:
        """Pre-load relevant context for context-enriched agents using ContextBuilder.

        Returns a context block to append to the system prompt, giving the
        agent ambient awareness of the user's world without requiring it to
        explicitly call search_memory.
        """
        if agent_name not in CONTEXT_ENRICHED_AGENTS:
            return ""

        sections: list[str] = []

        # Load integration identity context (GitHub username, Google email, etc.)
        integration_ctx = await self._load_integration_context(user_id, workspace_id)
        if integration_ctx:
            sections.append(integration_ctx)

        try:
            svc = self._services
            async with self._db_factory() as db:
                builder = ContextBuilder(
                    world_model=svc.world_model,
                    memory_service=svc.memory_service,
                    artifact_store=svc.artifact_store,
                    db=db,
                    graph_engine=svc.graph_engine,
                    tri_search=svc.tri_search,
                    reranker=svc.reranker,
                )
                pack: ContextPack = await builder.build(
                    user_id=user_id,
                    query=message[:500],
                    workspace_id=workspace_id,
                )
                context_text = ContextBuilder.to_prompt(pack)
                if context_text:
                    sections.append(context_text)
        except Exception:
            logger.debug("Context assembly via ContextBuilder failed", exc_info=True)

        if sections:
            return "\n\n--- CONTEXT ---\n" + "\n\n".join(sections)
        return ""

    async def _load_integration_context(self, user_id: str, workspace_id: str) -> str:
        """Load connected integration identities for agent context.

        Returns a compact text block with provider-specific identity info
        (e.g., GitHub username/orgs, Google email) so agents can fill in
        required tool parameters like 'owner'.
        """
        try:
            from sqlalchemy import select

            from src.models.integration_installation import IntegrationInstallation

            async with self._db_factory() as db:
                result = await db.execute(
                    select(IntegrationInstallation).where(
                        IntegrationInstallation.workspace_id == workspace_id,
                        IntegrationInstallation.status == "active",
                        IntegrationInstallation.enabled.is_(True),
                        IntegrationInstallation.config.isnot(None),
                    )
                )
                installations = result.scalars().all()

            lines: list[str] = []
            for inst in installations:
                config = inst.config or {}
                if not config:
                    continue

                if inst.server_name == "github" and config.get("username"):
                    line = f"- GitHub: username={config['username']}"
                    if config.get("organizations"):
                        line += f", orgs=[{', '.join(config['organizations'])}]"
                    lines.append(line)
                elif config.get("account_email"):
                    label = inst.display_name or inst.server_name
                    lines.append(f"- {label}: {config['account_email']}")

            if lines:
                return "Connected integrations (use these for tool parameters):\n" + "\n".join(
                    lines
                )
        except Exception:
            logger.debug("Integration context load failed", exc_info=True)
        return ""

    async def _apply_perception_policy_from_planner(
        self,
        planner_text: str,
        source: str,
        user_id: str,
        workspace_id: str,
        event_count: int,
    ) -> None:
        """Extract optional perception_policy from planner response and apply it.

        Falls back to deterministic defaults if the planner doesn't include
        a policy block or returns invalid JSON.
        """
        from src.orchestrator.contracts import PerceptionDecision
        from src.services.perception_policy import PerceptionPolicyService

        policy = self._extract_perception_policy(planner_text)
        if policy is None and event_count > 0:
            # Deterministic fallback: if events were found, check sooner
            policy = PerceptionDecision(
                next_check_seconds=120,
                urgency="normal",
                reasoning="events found, checking sooner",
            )

        if policy is None:
            return

        try:
            async with self._db_factory() as db:
                svc = PerceptionPolicyService(db)
                state = await svc.get_or_create_state(workspace_id, user_id, source)
                await svc.apply_agent_policy(
                    state,
                    next_check_seconds=policy.next_check_seconds,
                    watch_entities=policy.watch_entities if policy.watch_entities else None,
                )
                await db.commit()
        except Exception:
            logger.debug("Failed to apply perception policy", exc_info=True)

    async def _queue_perception_plan(
        self,
        planner_result: str,
        source: str,
        user_id: str,
        workspace_id: str,
        trace_id: str,
    ) -> PlanOutput | None:
        """Extract a structured plan from the Planner's perception response
        and queue actionable plans for background execution.

        System capability steps are handled inline. Steps with write
        capabilities are persisted as Plan + background TaskRun.
        """
        import hashlib

        plan = extract_plan(planner_result)

        # Check if any steps are actionable
        has_system_caps = any(
            s.capability.startswith("system.") for s in plan.steps if s.actor == "jarvis"
        )
        has_write_steps = any(s.risk not in ("none",) for s in plan.steps if s.actor == "jarvis")
        has_tool_steps = any(
            not s.capability.startswith("system.")
            and s.capability not in ("reason", "respond", "none")
            for s in plan.steps
            if s.actor == "jarvis"
        )

        if not has_system_caps and not has_write_steps and not has_tool_steps:
            logger.debug(
                "Perception plan from %s — no actionable steps",
                source,
            )
            return plan

        # Handle system capability steps inline
        inline_caps = {
            "system.set_goal",
            "system.set_instruction",
            "system.schedule_reminder",
            "system.add_to_brief",
        }
        for step in plan.steps:
            if step.capability in inline_caps:
                try:
                    await self._handle_system_capability(step, plan, user_id, workspace_id)
                    logger.info(
                        "Perception inline handler: %s from %s",
                        step.capability,
                        source,
                    )
                except Exception:
                    logger.warning(
                        "Perception inline handler failed: %s",
                        step.capability,
                        exc_info=True,
                    )

        # For steps requiring tool execution, persist and queue
        tool_steps = [
            s
            for s in plan.steps
            if s.actor == "jarvis"
            and not s.capability.startswith("system.")
            and s.capability not in ("reason", "respond", "none")
        ]
        if not tool_steps:
            return plan

        # Compute idempotency key
        goal_hash = hashlib.sha256((plan.goal or "").encode()).hexdigest()[:16]
        idempotency_key = f"perception:{source}:{goal_hash}"

        # Persist Plan + PlanTasks
        plan = await self._persist_plan_record(
            plan,
            user_id,
            workspace_id,
            trigger_type="perception",
            idempotency_key=idempotency_key,
        )

        if not plan.plan_id:
            logger.debug(
                "Plan not persisted (idempotent skip or error) for %s",
                source,
            )
            return plan

        # Create a background TaskRun for the scheduler
        try:
            async with self._db_factory() as db:
                from src.services.graph_executor import create_graph_executor

                executor = await create_graph_executor(
                    settings=self._settings,
                    db=db,
                    workspace_id=workspace_id,
                )
                run = await executor.create_run(
                    plan_id=plan.plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source="background",
                )
                await db.commit()

                logger.info(
                    "Perception queued plan %s → run %s from %s",
                    plan.plan_id,
                    run.run_id,
                    source,
                )
        except Exception:
            logger.warning(
                "Failed to create background run for perception plan %s",
                plan.plan_id,
                exc_info=True,
            )

        return plan

    async def _bump_perception_for_sources(
        self, sources: list[str], user_id: str, workspace_id: str
    ) -> None:
        """Signal immediate perception run for sources identified by intent classifier."""
        try:
            from src.services.perception_policy import PerceptionPolicyService

            async with self._db_factory() as db:
                svc = PerceptionPolicyService(db)
                for source in sources:
                    await svc.request_run(
                        workspace_id, user_id, source, signal_source="user_intent"
                    )
                await db.commit()
        except Exception:
            logger.warning("Failed to bump perception for sources", exc_info=True)

    @staticmethod
    def _extract_perception_policy(planner_text: str):
        """Parse a perception_policy JSON block from planner output, if present."""
        from src.orchestrator.contracts import PerceptionDecision

        if not planner_text or "perception_policy" not in planner_text:
            return None

        try:
            # Find the perception_policy JSON — could be embedded in markdown
            import re

            pattern = r'"perception_policy"\s*:\s*(\{[^}]+\})'
            match = re.search(pattern, planner_text)
            if not match:
                return None

            import json

            raw = json.loads(match.group(1))
            return PerceptionDecision(**raw)
        except Exception:
            logger.debug("Failed to parse perception_policy from planner", exc_info=True)
            return None

    def _build_system_prompt(
        self, agent: SubAgent, context: str = "", capability_summary: str = ""
    ) -> list[dict]:
        """Build system prompt with cache_control for prompt caching.

        For the Planner, injects the runtime capability summary into
        PLANNER_PROMPT_V2 (replacing the {capability_summary} placeholder).
        Other agents get JARVIS_SOUL_CORE + their role prompt unchanged.
        """
        soul = JARVIS_SOUL_CORE

        prompt = agent.prompt
        if agent.name == "planner":
            prompt = prompt.format(
                capability_summary=capability_summary or "No capabilities connected yet."
            )

        blocks = [
            {
                "type": "text",
                "text": f"{soul}\n\n--- YOUR ROLE ---\n{prompt}",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if context:
            blocks.append({"type": "text", "text": context})
        return blocks

    def _apply_cache_control_to_tools(self, tools: list[dict]) -> list[dict]:
        """Mark the last tool definition with cache_control for tool caching."""
        if not tools:
            return tools
        tools = [dict(t) for t in tools]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    async def _handle_set_goal(
        self,
        goal_text: str,
        reasoning: str,
        priority: str,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Store a goal as a memory via MemoryService."""
        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        title = goal_text or reasoning or "Untitled goal"
        memory_id = await memory_svc.store_goal_memory(
            user_id=user_id,
            workspace_id=workspace_id,
            title=title,
            priority=priority,
        )
        logger.info("Goal stored as memory %s: %s", memory_id, title)
        return {"status": "created", "memory_id": memory_id, "title": title}

    async def _handle_set_instruction(
        self,
        instruction_text: str,
        reasoning: str,
        instruction: dict,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Handle set_instruction: create trigger/schedule/preference memory."""
        if not instruction:
            return {"status": "error", "error": "No instruction spec provided"}

        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        inst_text = instruction.get("instruction_text", instruction_text)
        inst_type = instruction.get("instruction_type", "preference")

        # Store as a preference memory via public API
        memory_id = await memory_svc.store_instruction_memory(
            user_id=user_id,
            workspace_id=workspace_id,
            instruction_text=inst_text,
            instruction_type=inst_type,
        )

        result: dict = {
            "status": "created",
            "memory_id": memory_id,
            "instruction_type": inst_type,
            "text": inst_text,
        }

        trigger_conditions = instruction.get("trigger_conditions")
        schedule_config = instruction.get("schedule_config")

        # Create trigger if applicable
        if inst_type == "trigger" and trigger_conditions:
            try:
                from src.models.triggers import Trigger

                async with self._db_factory() as db:
                    trigger_id = f"trg_{ULID()}"
                    trigger = Trigger(
                        trigger_id=trigger_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        name=inst_text[:100],
                        conditions=trigger_conditions,
                        action_type="notify",
                        action_config={},
                        enabled=True,
                        status="active",
                    )
                    db.add(trigger)
                    await db.commit()
                result["trigger_id"] = trigger_id
            except Exception as e:
                logger.warning("Failed to create trigger: %s", e)

        # Create schedule if applicable
        if inst_type == "schedule" and schedule_config:
            try:
                from src.models.schedules import Schedule

                async with self._db_factory() as db:
                    schedule_id = f"sched_{ULID()}"
                    schedule = Schedule(
                        schedule_id=schedule_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        name=inst_text[:100],
                        schedule_type=schedule_config.get("type", "recurring"),
                        cron_expr=schedule_config.get("cron_expr"),
                        action_type=schedule_config.get("action_type", "custom_agent_task"),
                        action_config=schedule_config.get("action_config", {}),
                        enabled=True,
                        source="user",
                        priority="medium",
                    )
                    db.add(schedule)
                    await db.commit()
                result["schedule_id"] = schedule_id
            except Exception as e:
                logger.warning("Failed to create schedule: %s", e)

        logger.info("Instruction stored: %s (%s)", inst_text, inst_type)
        return result

    async def _handle_schedule_reminder(
        self,
        reminder_text: str,
        reasoning: str,
        tasks: list[dict],
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Create a one-shot schedule for a reminder."""
        from src.models.schedules import Schedule

        title = reminder_text or reasoning or "Reminder"
        # Extract timing from tasks if available
        schedule_config: dict = {}
        if tasks:
            schedule_config = tasks[0].get("input_data") or {}

        try:
            async with self._db_factory() as db:
                schedule_id = f"sched_{ULID()}"
                schedule = Schedule(
                    schedule_id=schedule_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    name=title[:100],
                    schedule_type="one_shot",
                    cron_expr=schedule_config.get("cron_expr"),
                    action_type="custom_agent_task",
                    action_config={
                        "instructions": f"Remind the user: {title}",
                        **schedule_config,
                    },
                    enabled=True,
                    source="user",
                    priority="medium",
                )
                db.add(schedule)
                await db.commit()

            logger.info("Reminder scheduled %s: %s", schedule_id, title)
            return {"status": "created", "schedule_id": schedule_id, "title": title}
        except Exception as e:
            logger.warning("Failed to create reminder schedule: %s", e)
            return {"status": "error", "error": str(e)}

    async def _handle_add_to_brief(self, text: str, user_id: str, workspace_id: str) -> dict:
        """Store a briefing item as a memory so the next briefing includes it."""
        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        text = text or "Briefing item"
        try:
            memory_id = await memory_svc.store_briefing_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                text=text,
            )
            logger.info("Briefing item stored as memory %s: %s", memory_id, text[:80])
            return {"status": "stored", "memory_id": memory_id, "text": text}
        except Exception as e:
            logger.warning("Failed to store briefing item: %s", e)
            return {"status": "error", "error": str(e)}

    async def _handle_system_capability(
        self,
        step: PlanStep,
        plan: PlanOutput,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Route system.* capability steps to direct handlers."""
        cap = step.capability

        if cap in ("system.respond", "system.acknowledge"):
            return {}

        known_system_caps = {
            "system.set_goal",
            "system.set_instruction",
            "system.schedule_reminder",
            "system.add_to_brief",
        }
        if cap not in known_system_caps:
            logger.warning("Unknown system capability: %s", cap)
            return {}

        goal_text = step.description or plan.goal
        reasoning = plan.reasoning

        # Execute the system capability
        result: dict = {}
        if cap == "system.set_goal":
            result = await self._handle_set_goal(
                goal_text, reasoning, plan.priority, user_id, workspace_id
            )
        elif cap == "system.set_instruction":
            instruction = step.input.get("instruction", {})
            result = await self._handle_set_instruction(
                goal_text, reasoning, instruction, user_id, workspace_id
            )
        elif cap == "system.schedule_reminder":
            tasks = step.input.get("tasks", [])
            result = await self._handle_schedule_reminder(
                goal_text, reasoning, tasks, user_id, workspace_id
            )
        elif cap == "system.add_to_brief":
            result = await self._handle_add_to_brief(goal_text, user_id, workspace_id)

        # Audit: record as completed PlanTask + InteractionLog
        if plan.plan_id:
            try:
                from src.models.interaction_log import InteractionLog
                from src.models.plans import PlanTask

                async with self._db_factory() as db:
                    db.add(
                        PlanTask(
                            task_id=f"ptask_{ULID()}",
                            plan_id=plan.plan_id,
                            workspace_id=workspace_id,
                            task_type=cap,
                            input_data=step.input or {"description": step.description},
                            status="completed",
                        )
                    )
                    db.add(
                        InteractionLog(
                            interaction_id=f"ilog_{ULID()}",
                            user_id=user_id,
                            workspace_id=workspace_id,
                            interaction_type=cap,
                            user_message=step.description[:500],
                            assistant_response=str(result)[:500] if result else "completed",
                            metadata_={"plan_step": step.step_id, "actor": "system"},
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to audit system capability step", exc_info=True)

        return result

    async def _call_agent(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
        capability_summary: str = "",
        tools_override: list[dict] | None = None,
    ) -> str:
        """Call a sub-agent (non-streaming). Returns final text response."""
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        model = self._get_model_for_agent(agent)

        if tools_override is not None:
            tools = self._apply_cache_control_to_tools(tools_override)
        else:
            tools = self._apply_cache_control_to_tools(
                await self._get_tools_for_agent(agent, workspace_id=workspace_id)
            )

        # Auto-generate capability summary for planner if not provided
        if agent_name == "planner" and not capability_summary:
            try:
                from src.orchestrator.capability_summary import (
                    generate_capability_summary,
                )

                async with self._db_factory() as db:
                    capability_summary = await generate_capability_summary(db, workspace_id)
            except Exception:
                logger.debug("Failed to generate capability summary", exc_info=True)

        context_block = await self._assemble_context(
            agent_name, message, user_id=user_id, workspace_id=workspace_id
        )
        system_blocks = self._build_system_prompt(
            agent, context_block, capability_summary=capability_summary
        )

        text = ""
        error = None
        async for evt in agent_loop(
            client=self._client,
            agent=agent,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=user_id,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            services=self._services,
            budget=self._budget,
            trace=trace,
            execute_tool_fn=self._execute_tool,
            max_tool_rounds=max_tool_rounds,
            stream=False,
            circuit_breaker=self._circuit_breaker,
        ):
            if isinstance(evt, LoopDone):
                text = evt.text
                logger.info(
                    "agent_call_complete",
                    extra={
                        "agent": agent_name,
                        "model": model,
                        "input_tokens": evt.input_tokens,
                        "output_tokens": evt.output_tokens,
                        "tools_called": evt.tools_called,
                        "latency_ms": evt.latency_ms,
                        "trace_id": trace.trace_id if trace else None,
                    },
                )
            elif isinstance(evt, LoopError):
                error = evt.message
                logger.warning(
                    "agent_call_failed",
                    extra={"agent": agent_name, "error": error},
                )

        if error and not text:
            return f"[Agent error: {error}]"
        return text

    async def _call_composite_tool(
        self, tool_name: str, tool_input: dict, user_id: str = "", workspace_id: str = ""
    ) -> dict:
        """Dispatch composite tools (multi-MCP orchestration)."""
        if tool_name == "web_search":
            from src.browser.web_search import web_search

            return await web_search(
                query=tool_input.get("query", ""),
                num_results=tool_input.get("num_results", 10),
                user_id=user_id,
                workspace_id=workspace_id,
            )
        return {"error": f"Unknown composite tool: {tool_name}"}

    # Cached in-process MCP client for internal tools
    _internal_client = None
    _internal_client_ctx = None

    async def _call_internal_tool(
        self, tool_name: str, tool_input: dict, server_prefix: str
    ) -> dict:
        """Call an internal tool via in-process FastMCP Client (MCP protocol).

        The composed server mounts tools under namespaced prefixes:
        - intelligence tools: "intelligence_" prefix
        - communication tools: "communication_" prefix
        We map flat tool names (e.g. "search", "send_telegram") to namespaced names
        (e.g. "intelligence_search", "communication_send_telegram").
        """
        import json

        from fastmcp import Client

        from src.tools.server import jarvis_tools

        # Lazy-init: create and cache the in-process client
        if self._internal_client is None:
            self._internal_client_ctx = Client(jarvis_tools)
            self._internal_client = await self._internal_client_ctx.__aenter__()

        # Map flat name to namespaced name (server-specific prefix)
        namespaced = f"{server_prefix}_{tool_name}"
        logger.info("[mcp:internal] calling %s (ns: %s)", tool_name, namespaced)
        result = await self._internal_client.call_tool(namespaced, tool_input)

        # Extract result from CallToolResult
        if result.is_error:
            error_text = result.data if hasattr(result, "data") else str(result)
            logger.warning("[mcp:internal] %s ERROR: %s", tool_name, str(error_text)[:200])
            return {"status": "error", "error": error_text}
        logger.info("[mcp:internal] %s OK", tool_name)

        # Parse structured content if available
        if hasattr(result, "structured_content") and result.structured_content:
            return result.structured_content.get("result", result.structured_content)

        # Fallback: parse text content as JSON
        text = result.data if hasattr(result, "data") else str(result)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"status": "ok", "result": text}
        return {"status": "ok", "result": text}

    async def _execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
        """Registry-driven dispatch: one lookup, one match on backend."""
        from src.services.tool_registry import ToolRegistry

        async with self._db_factory() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)
            tool = await registry.get_tool(tool_name)

        if not tool:
            logger.warning("[mcp] tool not found in registry: %s", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}
        if not tool.enabled:
            logger.warning("[mcp] tool disabled: %s", tool_name)
            return {"error": f"Tool '{tool_name}' is disabled", "blocked": True}

        # _special server returns input as-is (report_governor_verdict)
        if tool.backend == "internal_mcp" and tool.server == "_special":
            return tool_input

        logger.info(
            "[mcp] dispatch %s via %s/%s",
            tool_name,
            tool.backend,
            tool.server or "default",
        )
        await self._publish_event("tool.started", user_id, {"tool": tool_name})

        try:
            match tool.backend:
                case "internal_mcp":
                    # Internal tools receive workspace_id in input
                    if workspace_id and "workspace_id" not in tool_input:
                        tool_input = {**tool_input, "workspace_id": workspace_id}
                    result = await self._call_internal_tool(
                        tool_name,
                        {**tool_input, "user_id": user_id},
                        server_prefix=tool.server,
                    )
                case "external_mcp":
                    # External MCP servers do not accept workspace_id in tool input —
                    # it is passed as a keyword arg for session routing only.
                    from src.connectors.mcp_bridge import call_mcp_tool

                    result = await call_mcp_tool(
                        tool_name,
                        tool_input,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                case "composite":
                    # Composite tools are Jarvis-internal, receive workspace_id
                    if workspace_id and "workspace_id" not in tool_input:
                        tool_input = {**tool_input, "workspace_id": workspace_id}
                    result = await self._call_composite_tool(
                        tool_name,
                        tool_input,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                case _:
                    result = {"error": f"Unknown backend '{tool.backend}' for tool '{tool_name}'"}

            await self._publish_event("tool.completed", user_id, {"tool": tool_name})
            return result
        except Exception as e:
            logger.warning("[mcp] %s FAILED: %s", tool_name, e)
            await self._publish_event(
                "tool.failed",
                user_id,
                {"tool": tool_name, "error": str(e)[:200]},
            )
            return {"error": f"Tool execution failed for {tool_name}: {e}"}
