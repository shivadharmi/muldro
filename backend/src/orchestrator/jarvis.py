"""JarvisOrchestrator — the consciousness of Jarvis.

Routes user messages and system events to the right sub-agents,
manages traces, enforces budgets, and coordinates the intelligence loop.
This is the main entry point for all Jarvis interactions.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.models.task_graph import TaskRun, TaskStep
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
from src.orchestrator.contracts import PlannerOutput
from src.orchestrator.intent_classifier import (
    FAST_INTENTS,
    INTENT_CONFIDENCE_THRESHOLD,
    classify_intent,
    extract_decision,
    intent_to_decision,
)
from src.orchestrator.prompts import JARVIS_DECISION_FRAMEWORK, JARVIS_SOUL_CORE
from src.orchestrator.services import ServiceContainer
from src.orchestrator.tracing import TraceManager
from src.services.agent_registry import AgentRegistry
from src.services.context_builder import ContextBuilder, ContextPack
from src.services.execution_state import transition_run, transition_step
from src.services.route_resolver import RouteResolver
from src.services.trace_store import TraceStore
from src.tools.schemas import build_tool_definitions

logger = logging.getLogger(__name__)

# Event types published to the agent events stream
AGENT_EVENT_TYPES = {
    "plan_generated",
    "research_started",
    "research_completed",
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
    "researcher",
    "librarian",
    "operator",
    "governor",
}

# Intent classification constants imported from intent_classifier module


def _build_surface_preview(
    decision: PlannerOutput,
    kind: str,
    default_title: str,
    response_text: str,
):
    """Build a SurfacePreview from a planner decision for workspace grid cards.

    Extracts rich preview data per decision type: title, metrics, entities,
    progress, tags. The frontend renders this via SurfaceCard (not A2UI trees).
    """
    from src.ui.contracts import SurfaceMetric, SurfacePreview

    title = decision.goal[:80] if decision.goal else default_title
    subtitle = decision.reasoning[:120] if decision.reasoning else None
    metrics: list[SurfaceMetric] = []
    entities: list[str] = []
    tags: list[str] = []
    progress_val: float | None = None

    if kind == "plan":
        task_count = len(decision.tasks)
        if task_count:
            metrics.append(SurfaceMetric(label="Tasks", value=str(task_count)))
        metrics.append(SurfaceMetric(label="Priority", value=decision.priority))

    elif kind == "recommendation":
        tags.append("recommendation")
        if decision.risk_level != "none":
            variant = "warning" if decision.risk_level in ("high", "medium") else "default"
            metrics.append(SurfaceMetric(label="Risk", value=decision.risk_level, variant=variant))

    elif kind == "summary":
        tags.append(default_title.lower())

    elif kind == "briefing":
        tags.append("briefing")

    elif kind == "alert":
        tags.append("reminder")

    return SurfacePreview(
        title=title,
        subtitle=subtitle,
        status=None,
        priority=decision.priority if decision.priority != "medium" else None,
        metrics=metrics,
        entities=entities,
        progress=progress_val,
        tags=tags,
    )


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

    async def shutdown(self) -> None:
        """Await all pending background tasks on orchestrator shutdown."""
        if self._background_tasks:
            logger.info("Awaiting %d background tasks", len(self._background_tasks))
            await asyncio.wait(self._background_tasks, timeout=5.0)

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
        decision: PlannerOutput,
        user_id: str,
        workspace_id: str,
        trigger_type: str = "user_message",
        idempotency_key: str | None = None,
    ) -> PlannerOutput:
        """Persist a Plan + PlanTasks to DB, returning decision with plan_id populated.

        The Planner LLM agent returns a PlannerOutput but does NOT create DB
        records.  This method bridges that gap so the Governor can call
        evaluate_policy(plan_id) and the Operator can execute_plan via
        GraphExecutor — both of which require a DB-backed Plan.

        Args:
            trigger_type: Origin — "user_message" (interactive) or "perception"
                          (autonomous observation).
            idempotency_key: Optional dedup key to prevent duplicate perception plans.
        """
        from src.models.plans import Plan, PlanTask

        plan_id = f"plan_{ULID()}"

        try:
            async with self._db_factory() as db:
                # Idempotency check — skip if an active plan with this key exists
                if idempotency_key:
                    from sqlalchemy import select

                    existing = await db.execute(
                        select(Plan.plan_id).where(
                            Plan.idempotency_key == idempotency_key,
                            Plan.status.notin_(["completed", "failed", "cancelled"]),
                        )
                    )
                    if existing.scalar_one_or_none():
                        logger.info(
                            "Skipping duplicate plan: idempotency_key=%s",
                            idempotency_key,
                        )
                        return decision

                tasks = [
                    PlanTask(
                        task_id=f"ptask_{ULID()}",
                        plan_id=plan_id,
                        workspace_id=workspace_id,
                        task_type=task.task_type,
                        input_data=task.input_data,
                        status="pending",
                    )
                    for task in decision.tasks
                ]

                plan = Plan(
                    plan_id=plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    trigger_type=trigger_type,
                    trigger_ref=None,
                    idempotency_key=idempotency_key,
                    goal=decision.goal or "",
                    priority=decision.priority,
                    decision=decision.decision,
                    reasoning_summary=decision.reasoning or None,
                    risk_level=decision.risk_level,
                    execution_mode=decision.execution_mode,
                    status="created",
                )
                plan.tasks = tasks
                db.add(plan)
                await db.commit()

            logger.info(
                "Persisted plan %s decision=%s tasks=%d",
                plan_id,
                decision.decision,
                len(tasks),
            )
            return decision.model_copy(update={"plan_id": plan_id})
        except Exception:
            logger.warning("Failed to persist plan record", exc_info=True)
            return decision

    async def _create_lightweight_run(
        self,
        user_id: str,
        workspace_id: str,
        decision: PlannerOutput,
        trace_id: str,
        conversation_id: str | None = None,
    ) -> str | None:
        """Create a lightweight TaskRun for every user interaction.

        Even simple decisions (acknowledge, answer_directly) get a single-step
        run so ALL interactions are tracked in the runs table.
        Returns the run_id on success, None if DB unavailable.
        """
        run_id = f"run_{ULID()}"

        try:
            async with self._db_factory() as db:
                run = TaskRun(
                    run_id=run_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    plan_id=decision.plan_id,
                    status="running",
                    source="user_message",
                    execution_mode=decision.execution_mode,
                    policy_decision={"decision": decision.decision},
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    idempotency_key=(
                        f"{decision.plan_id}:{decision.decision}" if decision.plan_id else None
                    ),
                )
                db.add(run)

                step = TaskStep(
                    step_id=f"step_{ULID()}",
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id=f"task_{ULID()}",
                    plan_task_id=None,
                    step_type=decision.decision,
                    status="running",
                    input_data=decision.model_dump(mode="json"),
                )
                db.add(step)
                await db.commit()
        except Exception:
            logger.warning("Failed to create lightweight run", exc_info=True)
            return None

        return run_id

    async def _complete_lightweight_run(
        self,
        run_id: str,
        result: dict,
        success: bool = True,
    ) -> None:
        """Mark a lightweight run and its step as completed or failed."""
        try:
            async with self._db_factory() as db:
                from sqlalchemy import select

                res = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
                run = res.scalar_one_or_none()
                if not run:
                    return

                target_status = "completed" if success else "failed"
                try:
                    transition_run(run, target_status)
                except Exception:
                    run.status = target_status  # fallback for edge-case states
                if not success:
                    run.error = {"message": result.get("summary", "unknown error")[:500]}

                step_res = await db.execute(select(TaskStep).where(TaskStep.run_id == run_id))
                for step in step_res.scalars().all():
                    try:
                        transition_step(step, target_status)
                    except Exception:
                        step.status = target_status  # fallback for edge-case states
                    if success:
                        step.output_data = {
                            "decision": result.get("decision"),
                            "summary": str(result.get("summary", ""))[:1000],
                        }

                await db.commit()

                # D1: Learn from execution outcomes — store preference memories
                # based on approval decisions and failures for future context
                await self._learn_from_outcome(run_id, run, result, success)
        except Exception:
            logger.debug("Failed to complete lightweight run %s", run_id, exc_info=True)

    async def _learn_from_outcome(
        self,
        run_id: str,
        run,
        result: dict,
        success: bool,
    ) -> None:
        """Store preference/task_context memories from execution outcomes (D1).

        After a run completes, checks for linked approval decisions and
        failure context, then stores them as memories so future Planner
        calls have execution history context.
        """
        if not self._services.memory_service:
            return
        try:
            facts: list[str] = []

            async with self._db_factory() as db:
                # Check for linked approvals
                from sqlalchemy import select

                from src.models.approvals import Approval

                apr_result = await db.execute(
                    select(Approval).where(
                        Approval.run_id == run_id,
                        Approval.status.in_(["approved", "rejected"]),
                    )
                )
                for apr in apr_result.scalars().all():
                    fact = (
                        f"User {apr.status} '{apr.title}'"
                        f"{f' — reason: {apr.decision_reason}' if apr.decision_reason else ''}"
                    )
                    facts.append(fact)

            # Store failure context for the Planner
            if not success:
                goal = run.policy_decision.get("decision", "") if run.policy_decision else ""
                error_msg = result.get("summary", "unknown error")[:200]
                if goal:
                    facts.append(f"Plan '{goal}' failed: {error_msg}")

            if facts:
                await self._services.memory_service.extract_and_store(
                    user_id=run.user_id,
                    source_text="\n".join(facts),
                    source_event_ids=[run_id],
                    workspace_id=run.workspace_id,
                )
        except Exception:
            logger.debug("Outcome learning failed for run %s", run_id, exc_info=True)

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
                # Fallback to DB schema, then minimal.
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
                    schema = {"type": "object", "properties": {}}

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

        This is the main entry point for user interactions.
        The orchestrator decides which sub-agents to invoke.
        """
        if not user_id:
            return {"error": "user_id is required", "decision": "error"}
        if not workspace_id:
            return {"error": "workspace_id is required", "decision": "error"}
        if not message or not message.strip():
            return {"error": "Empty message", "decision": "ignore"}

        trace = self._trace_manager.start_trace("user_message")
        run_id: str | None = None

        try:
            # Emit command_received event
            await self._emit_runtime_event(
                "command_received",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"surface": surface, "message_preview": message[:100]},
            )

            # Load conversation history for multi-turn context
            history_block = await self._load_conversation_history(conversation_id)

            # Step 0: Fast intent classification
            intent, confidence, sources = await classify_intent(
                self._client, self._haiku_model, message, history_block
            )
            use_planner = intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD

            # Bump perception for relevant sources (fire-and-forget)
            if sources:
                await self._bump_perception_for_sources(sources, user_id, workspace_id)

            # Emit route_selected event
            await self._emit_runtime_event(
                "route_selected",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"intent": intent, "confidence": confidence, "use_planner": use_planner},
            )

            if use_planner:
                planner_message = f"User message: {message}\n\nContext: {json.dumps(context or {})}"
                if history_block:
                    planner_message = f"{history_block}\n\n{planner_message}"

                plan_result = await self._call_agent(
                    "planner",
                    message=planner_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
                decision = extract_decision(plan_result)
            else:
                decision = intent_to_decision(intent, message)

            # Persist Plan record so Governor and Operator can reference it
            if decision.tasks and not decision.plan_id:
                import hashlib

                goal_hash = hashlib.sha256((decision.goal or "").encode()).hexdigest()[:16]
                user_idem_key = f"user:{decision.decision}:{goal_hash}"
                decision = await self._persist_plan_record(
                    decision, user_id, workspace_id, idempotency_key=user_idem_key
                )

            decision_dict = decision.model_dump(mode="json")
            decision_json = json.dumps(decision_dict)

            # Create a lightweight TaskRun for tracking
            run_id = await self._create_lightweight_run(
                user_id=user_id,
                workspace_id=workspace_id,
                decision=decision,
                trace_id=trace.trace_id,
                conversation_id=conversation_id,
            )

            # Step 2: Resolve route dynamically from DB
            result = {
                "trace_id": trace.trace_id,
                "run_id": run_id,
                "decision": decision.decision,
                "summary": decision.reasoning or plan_result,
            }

            # Publish plan event
            await self._publish_event(
                "plan_generated",
                user_id,
                {"decision": decision_dict, "trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            # Handle direct decisions (no agent pipeline needed)
            if decision.decision == "set_goal":
                goal_result = await self._handle_set_goal(decision, user_id, workspace_id)
                result["goal"] = goal_result
            elif decision.decision == "set_instruction":
                instr_result = await self._handle_set_instruction(decision, user_id, workspace_id)
                result["instruction"] = instr_result
            elif decision.decision == "schedule_reminder":
                reminder_result = await self._handle_schedule_reminder(
                    decision, user_id, workspace_id
                )
                result["reminder"] = reminder_result
            elif decision.decision == "add_to_brief":
                brief_result = await self._handle_add_to_brief(decision, user_id, workspace_id)
                result["briefing_item"] = brief_result

            # Handle ignore decision — no response, no action
            if decision.decision == "ignore":
                result["status"] = "ignored"
                result["decision"] = "ignore"
                return result

            # Resolve agent pipeline from routes
            pipeline = await self._resolve_pipeline(decision_dict)

            for step in pipeline:
                agent_name = step.get("agent", "")
                if not agent_name or agent_name not in self._agents:
                    continue

                # Check step-level condition
                step_cond = step.get("condition")
                if step_cond and not self._check_step_condition(step_cond, decision_dict):
                    continue

                # Handle special actions
                action = step.get("action")
                if action == "execute_plan":
                    if decision.plan_id:
                        exec_result = await self._execute_plan_via_graph(
                            decision.plan_id, user_id, workspace_id, trace
                        )
                        result["execution"] = exec_result
                    continue

                # Format message from template
                template = step.get("message_template", "Process this: {decision_json}")
                agent_message = template.format(
                    decision_json=decision_json, surface=surface, message=message
                )

                agent_result = await self._call_agent(
                    agent_name,
                    message=agent_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
                result[agent_name] = agent_result

            # Step 3: Presenter formats the response (if not already in pipeline)
            if not any(s.get("agent") == "presenter" for s in pipeline):
                presenter_msg = f"Format this for the user ({surface}): {decision_json}"
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

            # Step 4: Persona learns from this interaction (fire-and-forget)
            try:
                await self._call_agent(
                    "persona",
                    message=f"Observe this user interaction on {surface}:\n"
                    f"User said: {message}\n"
                    f"Decision: {decision.decision}\n"
                    f"Extract any preference signals.",
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
            except Exception:
                logger.debug("Persona reflection skipped", exc_info=True)

            # Complete the lightweight run
            await self._complete_lightweight_run(run_id, result, success=True)
            await self._emit_runtime_event(
                "run_completed",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                payload={"trace_id": trace.trace_id},
            )

            # Push surface to workspace for visual decision types
            await self._push_workspace_surface(
                decision,
                user_id,
                workspace_id,
                run_id,
                response_text=result.get("presenter", result.get("summary", "")),
            )

            return result

        except Exception as e:
            logger.error("process_message failed: %s", e, exc_info=True)
            error_result = {
                "trace_id": trace.trace_id,
                "decision": "error",
                "summary": f"Error processing message: {e}",
            }
            if run_id:
                await self._complete_lightweight_run(run_id, error_result, success=False)
                await self._emit_runtime_event(
                    "run_failed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=run_id,
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
        """Stream events while processing a user message through the orchestrator.

        Yields SSE-compatible dicts with event types:
          agent_start, thinking, tool_call, tool_result, agent_done,
          response, error, done
        """
        if not user_id or not workspace_id:
            yield {"event": "error", "message": "user_id and workspace_id are required"}
            return
        if not message or not message.strip():
            yield {"event": "error", "message": "Empty message"}
            return

        trace = self._trace_manager.start_trace("user_message")
        run_id: str | None = None

        def _fire_event(event_type: str, **kwargs: Any) -> None:
            """Schedule a runtime event emission without blocking the SSE generator."""
            self._spawn_background(self._emit_runtime_event(event_type, **kwargs))

        try:
            yield {"event": "trace", "trace_id": trace.trace_id}

            # Emit command_received runtime event (fire-and-forget)
            _fire_event(
                "command_received",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"surface": surface, "message_preview": message[:100]},
            )

            # Load conversation history for multi-turn context
            history_block = await self._load_conversation_history(conversation_id)

            # Step 0: Fast intent classification (Haiku — <200ms)
            intent, confidence, sources = await classify_intent(
                self._client, self._haiku_model, message, history_block
            )
            yield {"event": "intent", "intent": intent, "confidence": confidence}

            # Bump perception for relevant sources (fire-and-forget)
            if sources:
                await self._bump_perception_for_sources(sources, user_id, workspace_id)

            # Decide routing based on intent AND mode
            # execute mode: always plan, then auto-execute
            # plan mode: always plan, but stop before execution
            # ask mode: use intent classification (current default)
            if mode == "execute":
                use_planner = True
            elif mode == "plan":
                use_planner = True
            else:
                use_planner = intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD

            # Emit route_selected runtime event (fire-and-forget)
            _fire_event(
                "route_selected",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"intent": intent, "confidence": confidence, "use_planner": use_planner},
            )

            decision: PlannerOutput
            plan_text = ""

            if use_planner:
                # Full Planner path for commands/complex intents
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

                decision = extract_decision(plan_text)
            else:
                # Fast path — synthesize a lightweight decision from intent
                decision = intent_to_decision(intent, message)

            # Apply mode overrides
            if mode == "execute" and decision.execution_mode != "auto_execute":
                decision = decision.model_copy(update={"execution_mode": "auto_execute"})
            elif mode == "plan" and decision.execution_mode != "draft_only":
                decision = decision.model_copy(update={"execution_mode": "draft_only"})

            # Persist Plan record so Governor and Operator can reference it
            if decision.tasks and not decision.plan_id:
                import hashlib

                goal_hash = hashlib.sha256((decision.goal or "").encode()).hexdigest()[:16]
                user_idem_key = f"user:{decision.decision}:{goal_hash}"
                decision = await self._persist_plan_record(
                    decision, user_id, workspace_id, idempotency_key=user_idem_key
                )

            decision_dict = decision.model_dump(mode="json")
            decision_json = json.dumps(decision_dict)

            # Create a lightweight TaskRun for tracking
            run_id = await self._create_lightweight_run(
                user_id=user_id,
                workspace_id=workspace_id,
                decision=decision,
                trace_id=trace.trace_id,
                conversation_id=conversation_id,
            )

            yield {
                "event": "decision",
                "decision": decision_dict,
                "run_id": run_id,
            }

            # Emit plan_created runtime event (fire-and-forget)
            _fire_event(
                "plan_created",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                payload={"decision": decision.decision, "trace_id": trace.trace_id},
            )

            # Handle direct decisions (no agent pipeline needed)
            if decision.decision == "set_goal":
                await self._handle_set_goal(decision, user_id, workspace_id)
            elif decision.decision == "set_instruction":
                await self._handle_set_instruction(decision, user_id, workspace_id)
            elif decision.decision == "schedule_reminder":
                await self._handle_schedule_reminder(decision, user_id, workspace_id)
            elif decision.decision == "add_to_brief":
                await self._handle_add_to_brief(decision, user_id, workspace_id)

            # Handle ignore decision — no response, no action
            if decision.decision == "ignore":
                yield {"event": "ignored", "decision": "ignore"}
                return

            # Step 2: Route based on intent
            if use_planner:
                # Planner path: resolve pipeline from DB routes
                pipeline = await self._resolve_pipeline(decision_dict)

                for step in pipeline:
                    agent_name = step.get("agent", "")
                    if not agent_name or agent_name not in self._agents:
                        continue

                    step_cond = step.get("condition")
                    if step_cond and not self._check_step_condition(step_cond, decision_dict):
                        continue

                    action = step.get("action")
                    if action == "execute_plan":
                        # Plan mode (draft_only): skip execution, just present the plan
                        if decision.execution_mode == "draft_only":
                            yield {
                                "event": "plan_ready",
                                "plan_id": decision.plan_id,
                                "message": "Plan created. Review and approve to execute.",
                            }
                            continue
                        if decision.plan_id:
                            yield {
                                "event": "execution_start",
                                "plan_id": decision.plan_id,
                            }
                            exec_result = await self._execute_plan_via_graph(
                                decision.plan_id, user_id, workspace_id, trace
                            )
                            yield {
                                "event": "execution_result",
                                "run_id": exec_result.get("run_id"),
                                "status": exec_result.get("status"),
                            }
                        continue

                    template = step.get("message_template", "Process this: {decision_json}")
                    agent_message = template.format(
                        decision_json=decision_json,
                        surface=surface,
                        message=message,
                    )

                    async for evt in self._call_agent_stream(
                        agent_name,
                        message=agent_message,
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    ):
                        yield evt

            elif intent == "simple_question":
                # Researcher gathers context, then Presenter responds
                async for evt in self._call_agent_stream(
                    "researcher",
                    message=f"Research this question for the user: {message}",
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        plan_text = f"Researcher findings:\n{evt.get('text', '')}"

            elif intent == "data_fetch":
                # Observer reads from external sources (Gmail, Calendar, Slack)
                observer_text = ""
                async for evt in self._call_agent_stream(
                    "observer",
                    message=(
                        f"The user wants to check an external source. "
                        f"Read the relevant data and report what you find.\n\n"
                        f"User request: {message}"
                    ),
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        observer_text = evt.get("text", "")

                # Feed observer results to presenter context
                if observer_text:
                    plan_text = f"Observer findings:\n{observer_text}"

            elif intent == "status_query":
                # Fetch status data via tools, then let Presenter format
                pass  # Presenter will handle with context enrichment below

            elif intent == "approval_response":
                # Governor handles approval directly
                async for evt in self._call_agent_stream(
                    "governor",
                    message=f"The user wants to approve/reject an action: {message}",
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        plan_text = f"Governor response:\n{evt.get('text', '')}"

            # Step 3: Presenter formats the response (always)
            presenter_msg = (
                f"Respond to the user ({surface}). Be conversational and helpful.\n\n"
                f"User message: {message}\n"
                f"Intent: {intent}\n"
            )
            if plan_text:
                presenter_msg += (
                    f"Planner decision: {decision_json}\nPlanner analysis: {plan_text[:2000]}\n"
                )
            if history_block:
                presenter_msg = f"{history_block}\n\n{presenter_msg}"

            presenter_text = ""
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
                    yield {"event": "response", "text": presenter_text}

            # Persona learning — only for meaningful interactions
            if intent in ("command", "complex"):
                try:
                    await self._call_agent(
                        "persona",
                        message=f"Observe this user interaction on {surface}:\n"
                        f"User said: {message}\n"
                        f"Decision: {decision.decision}\n"
                        f"Extract any preference signals.",
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    )
                except Exception:
                    pass

            # Complete the lightweight run
            if run_id:
                await self._complete_lightweight_run(
                    run_id,
                    {"decision": decision.decision, "summary": presenter_text},
                    success=True,
                )
                _fire_event(
                    "run_completed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=run_id,
                    payload={"trace_id": trace.trace_id},
                )

            # Push surface to workspace via Redis + persist to DB.
            # The chat page receives it via WebSocket; workspace page via REST polling.
            # SSE delivery removed to avoid duplicates (WS is the canonical path).
            self._spawn_background(
                self._push_workspace_surface(
                    decision,
                    user_id,
                    workspace_id,
                    run_id,
                    response_text=presenter_text,
                )
            )

            yield {"event": "done", "trace_id": trace.trace_id, "run_id": run_id}

        except Exception as e:
            logger.error("process_message_stream failed: %s", e, exc_info=True)
            if run_id:
                await self._complete_lightweight_run(run_id, {"summary": str(e)}, success=False)
                _fire_event(
                    "run_failed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=run_id,
                    payload={"error": str(e)[:200]},
                )
            yield {"event": "error", "message": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def _call_agent_stream(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Call a sub-agent with streaming, yielding SSE-compatible dicts."""
        agent = self._agents.get(agent_name)
        if not agent:
            yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
            return

        model = self._get_model_for_agent(agent)
        tools = self._apply_cache_control_to_tools(
            await self._get_tools_for_agent(agent, workspace_id=workspace_id)
        )
        context_block = await self._assemble_context(
            agent_name, message, user_id=user_id, workspace_id=workspace_id
        )
        system_blocks = self._build_system_prompt(agent, context_block)

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
            decision = await self._queue_perception_plan(
                planner_result,
                "synthesis",
                user_id,
                workspace_id,
                trace.trace_id,
            )
            return {
                "status": "completed",
                "decision": decision.decision if decision else "none",
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
                return {"status": "completed", "source": source, "events": 0}

            # Ingest raw events into normalized_events table
            event_summaries = await self._ingest_raw_events(raw_events, user_id, workspace_id)

            # Update the observation cursor
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

            # Step 5: Extract decision and queue execution if actionable
            perception_decision = await self._queue_perception_plan(
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
                    "decision": perception_decision.decision if perception_decision else None,
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
                "decision": perception_decision.decision if perception_decision else None,
                "plan_id": perception_decision.plan_id if perception_decision else None,
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
                    PlannerOutput(
                        decision="add_to_brief",
                        goal="Daily Briefing",
                        reasoning=str(result)[:200],
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

    async def _push_workspace_surface(
        self,
        decision: PlannerOutput,
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> None:
        """Push a typed surface to the workspace via Redis Pub/Sub.

        Uses the two-layer model: SurfacePreview (grid card) + DetailConfig
        (modal tabs). Only pushes for decision types that have visual value
        beyond the chat response — 5 chat-only decisions are filtered out.
        """
        from datetime import datetime, timedelta, timezone

        from src.orchestrator.contracts import WorkspaceSurfacePush
        from src.ui.renderer import build_detail_config

        # 9 decisions that produce workspace surfaces; 5 chat-only excluded
        surface_kind_map: dict[str, tuple[str, str]] = {
            "create_task": ("plan", "New Plan"),
            "draft_reply": ("recommendation", "Draft Reply"),
            "recommend": ("recommendation", "Recommendation"),
            "summarize": ("summary", "Summary"),
            "research": ("summary", "Research Results"),
            "read_source": ("summary", "Source Summary"),
            "observe": ("summary", "Observation"),
            "add_to_brief": ("briefing", "Briefing Update"),
            "schedule_reminder": ("alert", "Reminder Scheduled"),
        }
        mapping = surface_kind_map.get(decision.decision)
        if not mapping:
            return

        kind, default_title = mapping

        try:
            event_bus = await self._ensure_event_bus()
            if not event_bus:
                return

            from ulid import ULID

            surface_id = f"surf_{ULID()}"
            preview = _build_surface_preview(decision, kind, default_title, response_text)
            detail_config = build_detail_config(kind, surface_id)

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind=kind,
                preview=preview.model_dump(mode="json"),
                detail_config=detail_config.model_dump(mode="json") if detail_config else None,
                decision=decision.decision,
                source_run_id=run_id,
                response_preview=(response_text[:300] if response_text else None),
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps({"type": "surface", "surface": surface.model_dump(mode="json")})
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
                logger.debug("Failed to persist workspace surface to DB", exc_info=True)
        except Exception:
            logger.warning("Failed to push workspace surface", exc_info=True)

    async def _load_conversation_history(
        self, conversation_id: str | None, max_messages: int = 20, max_chars: int = 8000
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
                summary = await self._summarize_history(older)
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

    async def _summarize_history(self, lines: list[str]) -> str:
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
            return "".join(b.text for b in response.content if b.type == "text")
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
                    return f"\n\n--- CONTEXT ---\n{context_text}"
        except Exception:
            logger.debug("Context assembly via ContextBuilder failed", exc_info=True)

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

    # Decisions from perception that should trigger execution or inline handling
    PERCEPTION_ACTIONABLE_DECISIONS = {
        "create_task",
        "draft_reply",
        "research",
        "watcher_create",
        "schedule_reminder",
        "set_goal",
        "set_instruction",
        "add_to_brief",
    }

    # Subset handled inline (fast, no pipeline needed)
    _PERCEPTION_INLINE_DECISIONS = {
        "schedule_reminder",
        "set_goal",
        "set_instruction",
        "add_to_brief",
    }

    async def _queue_perception_plan(
        self,
        planner_result: str,
        source: str,
        user_id: str,
        workspace_id: str,
        trace_id: str,
    ) -> PlannerOutput | None:
        """Extract a structured decision from the Planner's perception response
        and queue actionable plans for background execution.

        Lightweight decisions (set_goal, schedule_reminder, etc.) are handled
        inline.  Heavier decisions with tasks (create_task, draft_reply) are
        persisted as Plan + background TaskRun so the scheduler's
        _tick_background_tasks() picks them up on the next 30s tick.

        Returns the extracted PlannerOutput, or None if no action was needed.
        """
        import hashlib

        decision = extract_decision(planner_result)

        if decision.decision not in self.PERCEPTION_ACTIONABLE_DECISIONS:
            logger.debug(
                "Perception decision '%s' from %s — no action needed",
                decision.decision,
                source,
            )
            return decision

        # Handle lightweight decisions inline (fast, no pipeline)
        if decision.decision in self._PERCEPTION_INLINE_DECISIONS:
            try:
                if decision.decision == "set_goal":
                    await self._handle_set_goal(decision, user_id, workspace_id)
                elif decision.decision == "set_instruction":
                    await self._handle_set_instruction(decision, user_id, workspace_id)
                elif decision.decision == "schedule_reminder":
                    await self._handle_schedule_reminder(decision, user_id, workspace_id)
                elif decision.decision == "add_to_brief":
                    await self._handle_add_to_brief(decision, user_id, workspace_id)
                logger.info(
                    "Perception inline handler: %s from %s",
                    decision.decision,
                    source,
                )
            except Exception:
                logger.warning(
                    "Perception inline handler failed: %s",
                    decision.decision,
                    exc_info=True,
                )
            return decision

        # For decisions with tasks, persist plan and queue for background execution
        if not decision.tasks:
            logger.debug(
                "Perception decision '%s' from %s has no tasks — skipping",
                decision.decision,
                source,
            )
            return decision

        # Compute idempotency key to prevent duplicate plans from re-observed events
        goal_hash = hashlib.sha256((decision.goal or "").encode()).hexdigest()[:16]
        idempotency_key = f"perception:{source}:{decision.decision}:{goal_hash}"

        # Persist Plan + PlanTasks
        decision = await self._persist_plan_record(
            decision,
            user_id,
            workspace_id,
            trigger_type="perception",
            idempotency_key=idempotency_key,
        )

        if not decision.plan_id:
            logger.debug(
                "Plan not persisted (idempotent skip or error) for %s",
                source,
            )
            return decision

        # Create a background TaskRun with steps for the scheduler to execute
        try:
            async with self._db_factory() as db:
                from src.services.graph_executor import create_graph_executor

                executor = await create_graph_executor(
                    settings=self._settings,
                    db=db,
                    workspace_id=workspace_id,
                )
                run = await executor.create_run(
                    plan_id=decision.plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source="background",
                )
                await db.commit()

                logger.info(
                    "Perception queued plan %s → run %s (%d tasks) from %s",
                    decision.plan_id,
                    run.run_id,
                    len(decision.tasks),
                    source,
                )
        except Exception:
            logger.warning(
                "Failed to create background run for perception plan %s",
                decision.plan_id,
                exc_info=True,
            )

        return decision

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

    def _build_system_prompt(self, agent: SubAgent, context: str = "") -> list[dict]:
        """Build system prompt with cache_control for prompt caching.

        Uses structured system blocks so the static soul + role prompt is cached
        across calls (5-min TTL), saving ~90% on re-reads of the system prompt.
        """
        # Only the Planner sees the decision framework; other agents just get the core soul
        soul = JARVIS_SOUL_CORE
        if agent.name == "planner":
            soul += "\n" + JARVIS_DECISION_FRAMEWORK

        blocks = [
            {
                "type": "text",
                "text": f"{soul}\n\n--- YOUR ROLE ---\n{agent.prompt}",
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

    async def _handle_set_goal(self, decision, user_id: str, workspace_id: str) -> dict:
        """Store a goal as a memory via MemoryService."""
        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        title = decision.goal or decision.reasoning or "Untitled goal"
        memory_id = await memory_svc.store_goal_memory(
            user_id=user_id,
            workspace_id=workspace_id,
            title=title,
            priority=decision.priority,
        )
        logger.info("Goal stored as memory %s: %s", memory_id, title)
        return {"status": "created", "memory_id": memory_id, "title": title}

    async def _handle_set_instruction(self, decision, user_id: str, workspace_id: str) -> dict:
        """Handle set_instruction: create trigger/schedule/preference memory."""
        spec = decision.instruction
        if not spec:
            return {"status": "error", "error": "No instruction spec provided"}

        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        # Store as a preference memory via public API
        memory_id = await memory_svc.store_instruction_memory(
            user_id=user_id,
            workspace_id=workspace_id,
            instruction_text=spec.instruction_text,
            instruction_type=spec.instruction_type,
        )

        result: dict = {
            "status": "created",
            "memory_id": memory_id,
            "instruction_type": spec.instruction_type,
            "text": spec.instruction_text,
        }

        # Create trigger if applicable
        if spec.instruction_type == "trigger" and spec.trigger_conditions:
            try:
                from ulid import ULID

                from src.models.triggers import Trigger

                async with self._db_factory() as db:
                    trigger_id = f"trg_{ULID()}"
                    trigger = Trigger(
                        trigger_id=trigger_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        name=spec.instruction_text[:100],
                        conditions=spec.trigger_conditions,
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
        if spec.instruction_type == "schedule" and spec.schedule_config:
            try:
                from ulid import ULID

                from src.models.schedules import Schedule

                async with self._db_factory() as db:
                    schedule_id = f"sched_{ULID()}"
                    schedule = Schedule(
                        schedule_id=schedule_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        name=spec.instruction_text[:100],
                        schedule_type=spec.schedule_config.get("type", "recurring"),
                        cron_expr=spec.schedule_config.get("cron_expr"),
                        action_type=spec.schedule_config.get("action_type", "custom_agent_task"),
                        action_config=spec.schedule_config.get("action_config", {}),
                        enabled=True,
                        source="user",
                        priority="medium",
                    )
                    db.add(schedule)
                    await db.commit()
                result["schedule_id"] = schedule_id
            except Exception as e:
                logger.warning("Failed to create schedule: %s", e)

        logger.info("Instruction stored: %s (%s)", spec.instruction_text, spec.instruction_type)
        return result

    async def _handle_schedule_reminder(self, decision, user_id: str, workspace_id: str) -> dict:
        """Create a one-shot schedule for a reminder."""
        from src.models.schedules import Schedule

        title = decision.goal or decision.reasoning or "Reminder"
        # Extract timing from tasks if available
        schedule_config = {}
        if decision.tasks:
            schedule_config = decision.tasks[0].input_data or {}

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
                    priority=decision.priority,
                )
                db.add(schedule)
                await db.commit()

            logger.info("Reminder scheduled %s: %s", schedule_id, title)
            return {"status": "created", "schedule_id": schedule_id, "title": title}
        except Exception as e:
            logger.warning("Failed to create reminder schedule: %s", e)
            return {"status": "error", "error": str(e)}

    async def _handle_add_to_brief(self, decision, user_id: str, workspace_id: str) -> dict:
        """Store a briefing item as a memory so the next briefing includes it."""
        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        text = decision.goal or decision.reasoning or "Briefing item"
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

    async def _call_agent(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
    ) -> str:
        """Call a sub-agent (non-streaming). Returns final text response."""
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        model = self._get_model_for_agent(agent)
        tools = self._apply_cache_control_to_tools(
            await self._get_tools_for_agent(agent, workspace_id=workspace_id)
        )
        context_block = await self._assemble_context(
            agent_name, message, user_id=user_id, workspace_id=workspace_id
        )
        system_blocks = self._build_system_prompt(agent, context_block)

        text = ""
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

    async def _execute_plan_via_graph(
        self, plan_id: str, user_id: str, workspace_id: str, trace=None
    ) -> dict:
        """Bridge: create a run from a plan and execute it via GraphExecutor.

        This is the critical connection between the orchestrator (agent routing)
        and the GraphExecutor (DAG execution). Without this bridge, plans generated
        by the Planner would never actually execute.
        """
        from src.services.context_builder import ContextBuilder
        from src.services.graph_executor import GraphExecutor
        from src.services.tool_registry import ToolRegistry

        try:
            async with self._db_factory() as db:
                svc = self._services
                tool_registry = ToolRegistry(db)

                context_builder = ContextBuilder(
                    world_model=svc.world_model,
                    memory_service=svc.memory_service,
                    artifact_store=svc.artifact_store,
                )

                async def get_credentials(connector_type: str) -> dict:
                    if svc.oauth_manager:
                        provider_map = {
                            "gmail": "google",
                            "calendar": "google",
                            "drive": "google",
                            "github": "github",
                            "slack": "slack",
                            "linear": "linear",
                            "notion": "notion",
                            "jira": "jira",
                        }
                        oauth_provider = provider_map.get(connector_type, connector_type)
                        token = await svc.oauth_manager.get_valid_token(user_id, oauth_provider)
                        if token:
                            return {"access_token": token}
                    return {}

                executor = GraphExecutor(
                    settings=self._settings,
                    db=db,
                    event_bus=self._event_bus,
                    notifier=svc.notifier,
                    tool_registry=tool_registry,
                    context_builder=context_builder,
                    connector_credentials_fn=get_credentials,
                    memory_service=svc.memory_service,
                    # Agent loop dependencies
                    db_factory=self._db_factory,
                    execute_tool_fn=self._execute_tool,
                    budget=self._budget,
                    circuit_breaker=getattr(self, "_circuit_breaker", None),
                )

                run = await executor.create_run(plan_id, user_id, workspace_id)

                await self._publish_event(
                    "execution_started",
                    user_id,
                    {"plan_id": plan_id, "run_id": run.run_id},
                    trace_id=trace.trace_id if trace else None,
                )

                completed_run = await executor.execute_run(
                    run.run_id,
                    trace_id=trace.trace_id if trace else None,
                )

                await self._publish_event(
                    "execution_completed",
                    user_id,
                    {
                        "plan_id": plan_id,
                        "run_id": run.run_id,
                        "status": completed_run.status,
                    },
                    trace_id=trace.trace_id if trace else None,
                )

                return {
                    "run_id": run.run_id,
                    "status": completed_run.status,
                    "error": completed_run.error,
                }
        except Exception as e:
            logger.error("Plan execution via graph failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _resolve_pipeline(self, decision: dict) -> list[dict]:
        """Resolve a planner decision to an agent pipeline via RouteResolver."""
        try:
            async with self._db_factory() as db:
                resolver = RouteResolver(db)
                return await resolver.resolve(decision)
        except Exception:
            logger.warning("Route resolution failed, using empty pipeline", exc_info=True)
            return []

    @staticmethod
    def _check_step_condition(condition: dict, decision: dict) -> bool:
        """Check if a pipeline step's condition is satisfied.

        Supported condition types (mirrors RouteResolver._matches_conditions):
          has_key:        value exists in decision dict
          not_has_key:    value does NOT exist in decision dict
          has_truthy_key: value exists AND is truthy (not None/empty/False)
          field:<name>:   decision[name] == value
          <key>: <value>: decision[key] == value (direct equality)
        """
        for key, value in condition.items():
            if key == "has_key":
                if value not in decision:
                    return False
            elif key == "not_has_key":
                if value in decision:
                    return False
            elif key == "has_truthy_key":
                if not decision.get(value):
                    return False
            elif key.startswith("field:"):
                field_name = key[len("field:") :]
                if decision.get(field_name) != value:
                    return False
            else:
                if decision.get(key) != value:
                    return False
        return True
