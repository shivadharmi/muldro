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

from src.config.models import BEDROCK_MODEL_TIERS, MODEL_TIERS
from src.config.settings import Settings, get_anthropic_client
from src.connectors.mcp_bridge import close_turn_sessions
from src.contracts import PlanOutput, PlanStep
from src.errors import (
    classify,
    new_correlation_id,
)
from src.integrations.turn_scope import turn_scope
from src.middleware.observability import get_correlation_id
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import AGENTS, SubAgent, build_agent_set
from src.orchestrator.budget import BudgetTracker
from src.orchestrator.chat_pipeline import (
    build_presenter_message,
    build_user_action_block,
    format_prior_results_for_presenter,
    format_prior_step_results,
    resolve_plan_routing,
)
from src.orchestrator.context_assembler import ContextAssembler
from src.orchestrator.core_events import (
    CoreEvent,
    IntentClassified,
    InteractionLogged,
    PlanModeStepSkipped,
    PlanReady,
    Presentation,
    RunCompleted,
    RunFailed,
    StepError,
    StepResult,
    SystemStepResult,
    TraceStarted,
    UserActionsReady,
    ValidationFailed,
    agent_event_from_sse,
    core_event_to_sse,
)
from src.orchestrator.event_publisher import EventPublisher
from src.orchestrator.intent_classifier import (
    FAST_INTENTS,
    INTENT_CONFIDENCE_THRESHOLD,
    classify_intent,
    extract_plan,
    intent_to_plan,
)
from src.orchestrator.plan_store import PlanStore
from src.orchestrator.presenter_skip import extract_perceiver_synthesis, single_read_step
from src.orchestrator.services import ServiceContainer
from src.orchestrator.surface_pusher import SurfacePusher
from src.orchestrator.system_capability_handler import SystemCapabilityHandler
from src.orchestrator.tool_executor import ToolExecutor
from src.orchestrator.tracing import TraceManager
from src.services.agent_registry import AgentRegistry
from src.services.capability_resolver import CapabilityResolver
from src.services.interaction_learner import InteractionLearner
from src.services.surface_mapping import (
    extract_surface_spec,
    strip_surface_blocks,
)
from src.services.trace_store import TraceStore

logger = logging.getLogger(__name__)

# Per-message planner JSON contract suffix (mirrors PLANNER_PROMPT_V2's
# <final_response_contract>; kept near the user message as a reminder so the
# final response parser doesn't get tripped by stray prose).
_PLANNER_JSON_CONTRACT_SUFFIX = (
    "\n\nRespond with a single PlanOutput JSON object — no prose, "
    "no preamble, no code fences. Start with { and end with }."
)

# Event types published to the agent events stream
AGENT_EVENT_TYPES = {
    "approval_requested",
    "execution_started",
    "execution_completed",
    "memory_updated",
    "entity_created",
    "briefing_generated",
    "perception_completed",
}

# MODEL_TIERS / BEDROCK_MODEL_TIERS now live in src.config.models (imported above)
# so assessor services can depend on them downward instead of importing upward
# from this orchestrator module.


# CONTEXT_ENRICHED_AGENTS now lives in context_assembler.py (its only consumer).

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
        self._system_capability_handler = SystemCapabilityHandler(db_factory, services, settings)
        self._client = get_anthropic_client(settings)
        self._trace_store = TraceStore(db_factory=db_factory)
        self._trace_manager = TraceManager(trace_store=self._trace_store)
        self._budget = BudgetTracker(
            daily_limit_usd=settings.daily_token_budget_usd,
            redis=getattr(services, "redis", None) if services else None,
        )
        # Start with hardcoded defaults, applying cheap mode (opus→sonnet +
        # halved thinking budgets) when JARVIS_CHEAP_MODE is set.
        self._agents: dict[str, SubAgent] = build_agent_set(AGENTS, settings.cheap_mode)

        # Collaborators resolve db_factory through a provider so this orchestrator
        # stays the single source of truth — reassigning self._db_factory (in
        # tests or at runtime) propagates to all of them.
        def _db_factory_provider():
            return self._db_factory

        # EventPublisher owns the lazy event bus + runtime-event emission (C5).
        self._events = EventPublisher(settings, services, _db_factory_provider)
        # ContextAssembler builds conversation-history + ambient context blocks.
        self._context = ContextAssembler(settings, services, _db_factory_provider, self._client)
        # PlanStore persists plans + interaction logs to the DB.
        self._plans = PlanStore(_db_factory_provider)
        # ToolExecutor builds tool definitions and dispatches tool calls.
        self._tool_executor = ToolExecutor(self._events, _db_factory_provider)
        # SurfacePusher builds + delivers A2UI workspace surfaces.
        self._surfaces = SurfacePusher(self._events, _db_factory_provider)
        self._background_tasks: set[asyncio.Task] = set()  # C2: track fire-and-forget tasks
        # C1: API circuit breaker — fail fast when Claude API is in sustained outage
        from src.orchestrator.api_circuit_breaker import AnthropicCircuitBreaker

        self._circuit_breaker = AnthropicCircuitBreaker()
        # AgentInvoker runs a single sub-agent through the agent loop — shared by
        # the chat (streaming) and perception (batch) paths. Depends on the tool
        # executor + context assembler; agent set is kept in sync via set_agents().
        self._invoker = AgentInvoker(
            settings,
            self._client,
            services,
            self._budget,
            self._circuit_breaker,
            _db_factory_provider,
            self._tool_executor,
            self._context,
            self._agents,
        )
        # Interaction learning — async memory extraction from user messages
        # The learner extracts memories via db_factory (per-op sessions), so it
        # only needs the session-free vector_store from the shared container —
        # not a DB-bound memory_service (which is None in the API/shared path).
        self._interaction_learner: InteractionLearner | None = None
        if db_factory is not None:
            self._interaction_learner = InteractionLearner(
                settings=settings,
                db_factory=db_factory,
                vector_store=self._services.vector_store if self._services else None,
                redis=None,  # Populated lazily when event bus Redis is available
            )
        # Precompute haiku model ID for intent classification
        if settings.use_bedrock:
            self._haiku_model = BEDROCK_MODEL_TIERS["haiku"]
        else:
            self._haiku_model = MODEL_TIERS["haiku"]

    def _request_services(self, db) -> ServiceContainer:
        """Return a ServiceContainer whose DB-bound services use ``db``.

        When a fully-built container was injected (tests, single-flow ``build``),
        reuse it as-is. In the API path the orchestrator holds only the shared
        session-free singletons (``build_shared``), so DB-bound services are
        built per request here — this is what stops concurrent requests from
        sharing one ``AsyncSession`` (P2 #4, reverses the old ADR §10).
        """
        from src.runtime import request_services

        return request_services(self._services, self._settings, db)

    def _spawn_background(self, coro) -> None:
        """Launch a background task with lifecycle tracking (C2)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _ensure_learner_deps(self) -> None:
        """Lazily wire Redis + EventBus into the interaction learner."""
        if not self._interaction_learner:
            return
        learner = self._interaction_learner
        if learner._redis is None or learner._event_bus is None:
            event_bus = await self._events.ensure_event_bus()
            if learner._redis is None and self._events.event_bus_redis is not None:
                learner._redis = self._events.event_bus_redis
            if learner._event_bus is None and event_bus:
                learner._event_bus = event_bus

    async def shutdown(self) -> None:
        """Await all pending background tasks on orchestrator shutdown.

        Tasks that don't finish within the grace period are cancelled (not
        abandoned silently) so we surface stuck work and let cancellation run.
        """
        if not self._background_tasks:
            return
        logger.info("Awaiting %d background tasks", len(self._background_tasks))
        _done, pending = await asyncio.wait(self._background_tasks, timeout=5.0)
        if pending:
            logger.warning(
                "Cancelling %d background task(s) that did not finish within 5s shutdown grace",
                len(pending),
            )
            for task in pending:
                task.cancel()
            # Allow cancellation to propagate; swallow the resulting CancelledErrors.
            await asyncio.gather(*pending, return_exceptions=True)

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
                    self._agents = build_agent_set(db_agents, self._settings.cheap_mode)
                    # Keep the invoker's agent set in sync (single source of truth).
                    self._invoker.set_agents(self._agents)
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
        """Delegate to PlanStore (facade kept for internal callers)."""
        return await self._plans.persist_plan_record(
            plan_output,
            user_id,
            workspace_id,
            trigger_type=trigger_type,
            idempotency_key=idempotency_key,
        )

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
        """Delegate to PlanStore (facade kept for internal callers)."""
        return await self._plans.log_interaction(
            user_id,
            workspace_id,
            trace_id,
            message_preview=message_preview,
            intent=intent,
            plan=plan,
            conversation_id=conversation_id,
            response_preview=response_preview,
            run_id=run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    async def _get_tools_for_agent(self, agent: SubAgent, workspace_id: str = "") -> list[dict]:
        """Delegate to ToolExecutor (facade kept for internal callers)."""
        return await self._tool_executor.get_tools_for_agent(agent, workspace_id=workspace_id)

    def _get_model_for_agent(self, agent: SubAgent) -> str:
        """Delegate to AgentInvoker (facade kept for internal callers)."""
        return self._invoker.get_model_for_agent(agent)

    async def process_message(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        conversation_id: str | None = None,
        surface: str = "api",
        context: dict | None = None,
        mode: str = "plan",
    ) -> dict:
        """Process a user message and return the batch ``result`` dict.

        Accumulating adapter over :meth:`_process_core`: validate inputs (the
        batch-shaped ``{"error": ...}``), drive the core to exhaustion, and fold
        its ``CoreEvent``s into the result dict that ``routes_ws`` returns
        verbatim. ``prompt_style="structured"`` selects the one-shot Presenter
        prompt (chat-pipeline-fold drift #1).

        ``mode`` defaults to ``"plan"`` (chat-pipeline-fold drift #6): the batch
        path is non-interactive, so risky (medium/high) steps are surfaced for
        approval rather than auto-executed, closing the latent ungated-background
        gap. Interactive callers (WS surface actions, where the user's click is
        authorization) override to ``"ask"``; pre-authorized scheduled automation
        (``custom_agent_task``) overrides to ``"execute"``. See the override map
        in ``routes_ws`` / ``schedule_dispatch``.
        """
        if not user_id:
            return {"error": "user_id is required"}
        if not workspace_id:
            return {"error": "workspace_id is required"}
        if not message or not message.strip():
            return {"error": "Empty message"}

        result: dict[str, Any] = {}
        error_result: dict[str, Any] | None = None
        async for event in self._process_core(
            message,
            user_id,
            workspace_id,
            surface=surface,
            mode=mode,
            prompt_style="structured",
            context=context,
            conversation_id=conversation_id,
        ):
            match event:
                case TraceStarted(trace_id=trace_id):
                    result["trace_id"] = trace_id
                    result["run_id"] = None
                case InteractionLogged(interaction_id=interaction_id):
                    result["interaction_id"] = interaction_id
                case PlanReady(plan=plan, run_id=run_id, summary=summary):
                    result["plan"] = plan
                    result["run_id"] = run_id
                    result["summary"] = summary
                case SystemStepResult(key=key, output=output):
                    result[key] = output
                case StepResult(key=key, output=output):
                    result[key] = output
                case StepError(step_id=step_id, error=error):
                    result[f"error_{step_id}"] = error
                case PlanModeStepSkipped(plan_id=plan_id, message=skip_message):
                    result.setdefault("plan_ready", []).append(
                        {"plan_id": plan_id, "message": skip_message}
                    )
                case UserActionsReady(steps=steps):
                    result["user_actions"] = steps
                case Presentation(text=text):
                    result["presentation"] = text
                case RunCompleted(surface_id=surface_id):
                    if surface_id:
                        result["surface_id"] = surface_id
                case RunFailed(
                    trace_id=trace_id, code=code, message=fail_message, correlation_id=cid
                ):
                    # Batch failure shape — distinct from the SSE error frame.
                    # Capture and KEEP DRAINING the generator: an early return
                    # would abandon _process_core suspended at `yield RunFailed`,
                    # skipping its `finally: finish_trace(...)` (trace leak).
                    error_result = {
                        "trace_id": trace_id,
                        "decision": "error",
                        "summary": fail_message,
                        "code": code,
                        "correlation_id": cid,
                    }
                case _:
                    # AgentStreamEvent / IntentClassified / typed agent events:
                    # batch drops token-level events. Explicit so a new CoreEvent
                    # is a deliberate drop, not a silent one.
                    pass
        return error_result if error_result is not None else result

    async def process_message_events(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        surface: str = "web",
        mode: str = "ask",
        context: dict | None = None,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[CoreEvent, None]:
        """Public typed-event entry point for the conversational (streaming) path.

        Validate inputs (yielding a typed :class:`ValidationFailed`), then drive
        :meth:`_process_core` with ``prompt_style="conversational"`` (the live-chat
        Presenter prompt, chat-pipeline-fold drift #1). Consumers that want SSE
        dicts use :meth:`process_message_stream`; consumers that fold typed events
        (``routes_chat``) consume this directly via :func:`core_event_to_sse`.
        """
        if not user_id or not workspace_id:
            yield ValidationFailed(message="user_id and workspace_id are required")
            return
        if not message or not message.strip():
            yield ValidationFailed(message="Empty message")
            return

        async for event in self._process_core(
            message,
            user_id,
            workspace_id,
            surface=surface,
            mode=mode,
            prompt_style="conversational",
            context=context,
            conversation_id=conversation_id,
        ):
            yield event

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
        """Stream SSE-compatible event dicts while processing a user message.

        Thin SSE adapter over :meth:`process_message_events`: translate each
        ``CoreEvent`` to its SSE dict, dropping batch-only events (``None``).
        """
        async for event in self.process_message_events(
            message,
            user_id,
            workspace_id,
            surface=surface,
            mode=mode,
            context=context,
            conversation_id=conversation_id,
        ):
            sse = core_event_to_sse(event)
            if sse is not None:
                yield sse

    async def _process_core(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        *,
        surface: str,
        mode: str,
        prompt_style: str,
        context: dict | None,
        conversation_id: str | None,
    ) -> AsyncGenerator[CoreEvent, None]:
        """Unified chat-orchestration pipeline shared by both public entry points.

        Drives the single intent → plan → route → execute → present → surface →
        learn sequence and yields typed ``CoreEvent``s. ``process_message_stream``
        translates them to SSE; ``process_message`` folds them into the batch
        result dict. Assumes inputs are already validated by the calling adapter;
        owns the trace lifecycle and the terminal ``RunFailed`` on exception.

        Runtime events fire in the background (the stream path's discipline,
        adopted for both per chat-pipeline-fold drift #4).
        """
        trace = self._trace_manager.start_trace("user_message")

        async with turn_scope(on_close=close_turn_sessions):

            def _fire_event(event_type: str, **kwargs: Any) -> None:
                self._spawn_background(self._emit_runtime_event(event_type, **kwargs))

            try:
                yield TraceStarted(trace_id=trace.trace_id)

                _fire_event(
                    "command_received",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    payload={"surface": surface, "message_preview": message[:100]},
                )

                history_block = await self._load_conversation_history(
                    conversation_id, user_id=user_id
                )

                # Step 0: Fast intent classification
                intent, confidence, sources = await classify_intent(
                    self._client, self._haiku_model, message, history_block
                )
                yield IntentClassified(intent=intent, confidence=confidence)

                if sources:
                    await self._bump_perception_for_sources(sources, user_id, workspace_id)

                # Decide routing based on intent AND mode
                if mode in ("execute", "plan"):
                    use_planner = True
                else:
                    use_planner = (
                        intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD
                    )

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
                    planner_message = (
                        f"User message: {message}\n\nContext: {json.dumps(context or {})}"
                        f"{_PLANNER_JSON_CONTRACT_SUFFIX}"
                    )
                    if history_block:
                        planner_message = f"{history_block}\n\n{planner_message}"

                    async for evt in self._call_agent_stream(
                        "planner",
                        message=planner_message,
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    ):
                        yield agent_event_from_sse(evt)
                        if evt.get("event") == "agent_done":
                            plan_text = evt.get("text", "")

                    plan = extract_plan(plan_text)
                else:
                    capabilities = await self._get_available_capabilities(workspace_id)
                    plan = intent_to_plan(intent, message, capabilities)

                # Apply mode overrides
                if mode == "plan":
                    plan = plan.model_copy(update={"requires_user_input": True})

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

                ilog_id = await self._log_interaction(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    trace_id=trace.trace_id,
                    message_preview=message[:500],
                    intent=intent,
                    plan=plan,
                    conversation_id=conversation_id,
                )
                yield InteractionLogged(interaction_id=ilog_id)

                yield PlanReady(plan=plan_dict, run_id=None, summary=plan.reasoning or plan_text)

                _fire_event(
                    "plan_created",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=None,
                    payload={"goal": plan.goal, "trace_id": trace.trace_id},
                )

                # Step 2: Pre-resolve routing and tools for all steps
                step_routing, user_steps = await resolve_plan_routing(
                    self._db_factory, workspace_id, plan.steps
                )

                # Step 3: Execute steps. `step_outputs` is the narrow prior-context
                # accumulator (agent step text only) injected into downstream agents
                # — kept separate from the batch result contract so plan/trace
                # metadata never leaks into agent prompts (drift #2).
                presenter_text = ""
                step_outputs: dict[str, str] = {}
                for step_idx, (step, agent_name, tools) in enumerate(step_routing):
                    if step.capability.startswith("system."):
                        sys_result = await self._handle_system_capability(
                            step, plan, user_id, workspace_id
                        )
                        yield SystemStepResult(key=f"system_{step.capability}", output=sys_result)
                        continue

                    if not agent_name:
                        error_msg = f"No tools available for capability '{step.capability}'"
                        logger.warning(error_msg)
                        yield StepError(step_id=step.step_id, error=error_msg)
                        continue

                    # Plan mode: skip risky execution, present the plan
                    if mode == "plan" and step.risk in ("medium", "high"):
                        yield PlanModeStepSkipped(
                            plan_id=plan.plan_id,
                            message="Plan created. Review and approve to execute.",
                        )
                        continue

                    agent_message = (
                        f"Execute this step: {step.description}\n"
                        f"Goal: {plan.goal}\n"
                        f"User message: {message}"
                    )
                    # Inject prior step results so downstream agents see earlier outputs.
                    agent_message += format_prior_step_results(step_outputs)
                    if history_block:
                        agent_message = f"{history_block}\n\n{agent_message}"

                    step_key = f"step_{step_idx}_{step.capability}"
                    async for evt in self._call_agent_stream(
                        agent_name,
                        message=agent_message,
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                        tools_override=tools if tools else None,
                    ):
                        yield agent_event_from_sse(evt)
                        if evt.get("event") == "agent_done":
                            done_text = evt.get("text", "")
                            yield StepResult(key=step_key, output=done_text)
                            # Capture step outputs for downstream agents (truthy only).
                            if done_text:
                                step_outputs[step_key] = done_text
                            # Capture text from respond/reason steps for surface preview.
                            if step.capability in ("reason", "respond"):
                                presenter_text = done_text

                # Build user action block from user_steps
                user_action_block = ""
                if user_steps:
                    user_action_block = build_user_action_block(user_steps)
                    yield UserActionsReady(
                        steps=[
                            {"description": s.description, "context": s.user_context}
                            for s in user_steps
                        ]
                    )

                # Latency: when the whole plan is one read-only Perceiver step,
                # return that read's own `synthesis` prose directly and skip the
                # Presenter LLM call (presenter_skip.py). Use the explicit
                # suffix-match (deterministic for multi-output; drift #3).
                direct_answer = None
                read_step = single_read_step(step_routing, user_steps)
                if read_step is not None and step_outputs:
                    read_key = next(
                        (k for k in step_outputs if k.endswith(f"_{read_step.capability}")), None
                    )
                    if read_key:
                        direct_answer = extract_perceiver_synthesis(step_outputs[read_key])

                # Step 4: Presenter formatting — unless we already have the read
                # agent's own answer above. system.respond steps are no-ops in
                # _handle_system_capability and reason/respond steps execute with
                # the wrong context, so for anything other than a single read we
                # still call the Presenter or the chat is left empty.
                if direct_answer is not None:
                    presenter_text = direct_answer
                    yield Presentation(text=direct_answer)
                else:
                    # Collect prior step results so Presenter can reference them.
                    prior_results_block = format_prior_results_for_presenter(step_outputs)
                    presenter_msg = build_presenter_message(
                        prompt_style=prompt_style,
                        surface=surface,
                        message=message,
                        intent=intent,
                        plan_dict=plan_dict,
                        plan_text=plan_text,
                        prior_results_block=prior_results_block,
                        user_action_block=user_action_block,
                        history_block=history_block,
                    )
                    async for evt in self._call_agent_stream(
                        "presenter",
                        message=presenter_msg,
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    ):
                        yield agent_event_from_sse(evt)
                        if evt.get("event") == "agent_done":
                            presenter_text = evt.get("text", "")
                            # Strip fenced surface blocks for the chat-visible reply
                            # while keeping presenter_text raw for surface extraction.
                            yield Presentation(text=strip_surface_blocks(presenter_text))

                _fire_event(
                    "run_completed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=None,
                    payload={"trace_id": trace.trace_id},
                )

                # Push workspace surface (Presenter-driven). Keep presenter_text raw
                # for extraction — it still carries the fenced surface blocks.
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
                    await self._ensure_learner_deps()
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

                yield RunCompleted(trace_id=trace.trace_id, run_id=None, surface_id=surface_id)

            except Exception as e:
                logger.error("_process_core failed: %s", e, exc_info=True)
                cid = get_correlation_id() or new_correlation_id()
                code, safe_msg, _ = classify(e)
                _fire_event(
                    "run_failed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=None,
                    payload={"code": code, "message": safe_msg, "correlation_id": cid},
                )
                yield RunFailed(
                    trace_id=trace.trace_id, code=code, message=safe_msg, correlation_id=cid
                )
            finally:
                await self._trace_manager.finish_trace(
                    trace.trace_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                )

    def _call_agent_stream(
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
        """Delegate to AgentInvoker (facade kept for internal callers)."""
        return self._invoker.call_agent_stream(
            agent_name,
            message,
            user_id,
            trace=trace,
            max_tool_rounds=max_tool_rounds,
            workspace_id=workspace_id,
            capability_summary=capability_summary,
            tools_override=tools_override,
        )

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
            code, safe_msg, _ = classify(e)
            return {
                "status": "error",
                "error": safe_msg,
                "code": code,
                "correlation_id": get_correlation_id() or new_correlation_id(),
            }
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
            # MCP-only integrations (e.g., Atlassian, Slack MCP server) have no
            # CONNECTOR_REGISTRY entry — their data flows entirely through
            # external MCP servers. Perception polling via native connectors
            # doesn't apply; short-circuit here so the scheduler doesn't log
            # perception_poll_failed warnings every tick for these sources.
            from src.connectors.base import CONNECTOR_REGISTRY

            if source not in CONNECTOR_REGISTRY:
                logger.debug(
                    "perception_skipped_mcp_only",
                    extra={"source": source},
                )
                return {"status": "skipped", "reason": "mcp_only_source", "source": source}

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

            # Ingest raw events into normalized_events table.
            # The cursor upsert is folded into the ingest session so that
            # "events ingested ⟹ cursor advanced" is a single gated commit —
            # the cursor only advances if the event loop ran to completion.
            event_summaries = await self._ingest_raw_events(
                raw_events,
                user_id,
                workspace_id,
                source=source,
                new_cursor=new_cursor,
                cursor_type=cursor_type,
            )

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

                # Fetch engagement context + deterministic dismissal penalty.
                # is_suppressed() hard-stops 5+-dismissal signal types; the
                # graduated penalty (3-4 dismissals → 0.2) is applied to the
                # assessor score so borderline signals are demoted a tier.
                engagement_context = ""
                relevance_penalty = 0.0
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
                        relevance_penalty = await eng_svc.get_relevance_penalty(
                            signal.source, signal.event_type
                        )
                except Exception:
                    logger.debug("Failed to load engagement context", exc_info=True)

                assessment = await assess_relevance(
                    signal,
                    user_context,
                    self._client,
                    engagement_context=engagement_context,
                    relevance_penalty=relevance_penalty,
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
                workspace_id=workspace_id,
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
            code, safe_msg, _ = classify(e)
            return {
                "status": "error",
                "source": source,
                "error": safe_msg,
                "code": code,
                "correlation_id": get_correlation_id() or new_correlation_id(),
            }
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
                    ObservationCursor.workspace_id == workspace_id,
                    ObservationCursor.user_id == user_id,
                    ObservationCursor.source == source,
                )
            )
            row = result.first()
            if row:
                cursor = row[0]

        try:
            from src.connectors.poll_result import PollResult, error_class_to_policy_error

            result = await asyncio.wait_for(
                connector.poll(user_id, cursor, {"access_token": access_token}),
                timeout=30,
            )

            # Connectors now return PollResult; accept legacy 2-tuple for safety.
            if isinstance(result, PollResult):
                if result.failed:
                    # Sentinel message contains the keyword classify_error() needs;
                    # prefix with source for observability without repeating error_class.
                    policy_err = error_class_to_policy_error(result.error_class)
                    error_msg = f"Poll failed for {source}: {policy_err}"
                    logger.warning(
                        "connector_poll_error",
                        extra={
                            "source": source,
                            "error_class": result.error_class,
                            "error": error_msg[:500],
                        },
                    )
                    # Return unchanged cursor — never advance on failure
                    return [], result.cursor, error_msg, cursor_type
                return result.events, result.cursor, None, cursor_type
            else:
                # Legacy 2-tuple fallback (non-native connectors)
                events, new_cursor = result
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

    @staticmethod
    def _build_cursor_upsert_stmt(
        source: str,
        user_id: str,
        workspace_id: str,
        new_cursor: str,
        cursor_type: str,
    ):
        """Return a pg ``INSERT … ON CONFLICT DO UPDATE`` statement for the
        observation cursor.  Both the ingest path and the empty-poll path use
        this builder so the SQL shape is never duplicated.

        The caller is responsible for executing the statement on its own
        ``db`` session; this function performs no I/O.
        """
        from datetime import datetime, timezone

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from src.models.observation_cursor import ObservationCursor

        now = datetime.now(timezone.utc)
        return (
            pg_insert(ObservationCursor)
            .values(
                cursor_id=f"cur_{ULID()}",
                user_id=user_id,
                workspace_id=workspace_id,
                source=source,
                cursor_type=cursor_type,
                cursor_value=new_cursor,
                last_observation_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_cursor_ws_user_source",
                set_={
                    "cursor_value": new_cursor,
                    "cursor_type": cursor_type,
                    "last_observation_at": now,
                },
            )
        )

    async def _ingest_raw_events(
        self,
        raw_events: list,
        user_id: str,
        workspace_id: str,
        *,
        source: str = "",
        new_cursor: str | None = None,
        cursor_type: str = "opaque",
    ) -> list[str]:
        """Ingest raw events into the event processor. Returns summary strings.

        ``EventProcessor.process()`` commits **per event** internally, so by
        the time the loop finishes the session may have issued many commits.
        When *new_cursor* is also provided, the cursor upsert is executed on
        the **same** session after the loop and committed by the single trailing
        ``await db.commit()`` at the end of this method.

        The invariant guaranteed here is narrower than a single transaction:
        **the cursor is not advanced unless the event loop ran to completion**
        (i.e. no ``new_cursor`` write happens if the session or
        ``EventProcessor`` construction raises before the loop starts).
        Per-event commit failures are caught and forwarded to the DLQ; they do
        not prevent the cursor from advancing for the events that succeeded.
        """
        summaries = []
        async with self._db_factory() as db:
            from src.services.dead_letter import DeadLetterService
            from src.services.event_processor import EventProcessor

            req = self._request_services(db)
            event_bus = await self._ensure_event_bus()
            dead_letter = DeadLetterService(db)

            processor = EventProcessor(
                self._settings,
                db,
                world_model=req.world_model,
                memory_service=req.memory_service,
                dead_letter=dead_letter,
                event_bus=event_bus,
                notifier=req.notifier,
                embedding_service=req.extras.get("embedding_service"),
                vector_store=req.vector_store,
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

            # Advance the cursor on the same session so it is not written
            # unless the event loop ran to completion.
            if new_cursor and source:
                stmt = self._build_cursor_upsert_stmt(
                    source, user_id, workspace_id, new_cursor, cursor_type
                )
                await db.execute(stmt)
            elif new_cursor and not source:
                logger.warning(
                    "ingest_cursor_skipped_no_source",
                    extra={"new_cursor": new_cursor, "user_id": user_id},
                )

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
        """Update the observation cursor after a successful poll.

        Uses a single ``INSERT ... ON CONFLICT DO UPDATE`` so concurrent
        perception cycles for the same ``(workspace_id, user_id, source)``
        cannot race on the ``uq_cursor_ws_user_source`` unique constraint.

        This method is used by the **empty-poll path** so incremental sync
        tokens (e.g. Gmail historyId, Calendar syncToken) advance even when
        no new events were returned.  The non-empty-poll path folds the cursor
        write directly into ``_ingest_raw_events`` instead.
        """
        if not new_cursor:
            return
        async with self._db_factory() as db:
            stmt = self._build_cursor_upsert_stmt(
                source, user_id, workspace_id, new_cursor, cursor_type
            )
            await db.execute(stmt)
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
                workspace_id=workspace_id,
                trace_id=trace.trace_id,
            )

            # B3: Deliver briefing to user via notifications + workspace surface
            try:
                async with self._db_factory() as db:
                    req = self._request_services(db)
                    if req.notifier:
                        await req.notifier.notify(
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
            code, safe_msg, _ = classify(e)
            return {
                "status": "error",
                "error": safe_msg,
                "code": code,
                "correlation_id": get_correlation_id() or new_correlation_id(),
            }
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def _ensure_event_bus(self):
        """Delegate to EventPublisher (facade kept for internal callers)."""
        return await self._events.ensure_event_bus()

    async def _publish_event(
        self,
        event_type: str,
        user_id: str,
        payload: dict,
        workspace_id: str = "",
        trace_id: str | None = None,
    ) -> None:
        """Delegate to EventPublisher (facade kept for internal callers)."""
        await self._events.publish_event(
            event_type, user_id, payload, workspace_id=workspace_id, trace_id=trace_id
        )

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
        """Delegate to EventPublisher (facade kept for internal callers)."""
        await self._events.emit_runtime_event(
            event_type,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            step_id=step_id,
            payload=payload,
        )

    async def _check_surface_rate(self, user_id: str, surface_type: str) -> bool:
        """Delegate to SurfacePusher (facade kept for internal callers)."""
        return await self._surfaces.check_surface_rate(user_id, surface_type)

    async def _push_presenter_surface(
        self,
        spec,
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> str | None:
        """Delegate to SurfacePusher (facade kept for internal callers)."""
        return await self._surfaces.push_presenter_surface(
            spec, user_id, workspace_id, run_id=run_id, response_text=response_text
        )

    async def _push_workspace_surface(
        self,
        plan: "PlanOutput",
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> str | None:
        """Delegate to SurfacePusher (facade kept for internal callers)."""
        return await self._surfaces.push_workspace_surface(
            plan, user_id, workspace_id, run_id=run_id, response_text=response_text
        )

    async def _push_insight_surface(
        self,
        signal: "PerceptionSignal",
        assessment: "RelevanceAssessment",
        user_id: str,
        workspace_id: str,
    ) -> None:
        """Delegate to SurfacePusher (facade kept for internal callers)."""
        await self._surfaces.push_insight_surface(signal, assessment, user_id, workspace_id)

    async def _load_conversation_history(
        self,
        conversation_id: str | None,
        max_messages: int = 20,
        max_chars: int = 20000,
        user_id: str = "",
    ) -> str:
        """Delegate to ContextAssembler (facade kept for internal callers)."""
        return await self._context.load_conversation_history(
            conversation_id, max_messages=max_messages, max_chars=max_chars, user_id=user_id
        )

    async def _assemble_context(
        self, agent_name: str, message: str, user_id: str, workspace_id: str = ""
    ) -> str:
        """Delegate to ContextAssembler (facade kept for internal callers)."""
        return await self._context.assemble_context(
            agent_name, message, user_id, workspace_id=workspace_id
        )

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
        from src.contracts import PerceptionDecision
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
            # No action to take, but the Planner may have produced a
            # cross-cutting insight (esp. on the synthesis path, which has no
            # prior relevance-routing step). Surface it as a briefing item so
            # the reasoning isn't silently discarded.
            if plan.goal and plan.goal.strip():
                try:
                    from src.services.memory_service import MemoryService

                    insight_text = plan.goal.strip()
                    if plan.reasoning and plan.reasoning.strip():
                        insight_text = f"{insight_text}\n\n{plan.reasoning.strip()}"
                    async with self._db_factory() as db:
                        mem_svc = MemoryService(self._settings, db)
                        await mem_svc.store_briefing_memory(
                            user_id=user_id,
                            workspace_id=workspace_id,
                            text=insight_text,
                            source=f"perception:{source}",
                            signal_source=source,
                        )
                        await db.commit()
                    logger.info(
                        "Perception insight from %s surfaced as briefing item",
                        source,
                    )
                except Exception:
                    logger.warning(
                        "Failed to surface non-actionable perception insight from %s",
                        source,
                        exc_info=True,
                    )
            else:
                logger.debug(
                    "Perception plan from %s — no actionable steps, no insight",
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
        from src.contracts import PerceptionDecision

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
        """Delegate to AgentInvoker (facade kept for internal callers)."""
        return self._invoker.build_system_prompt(
            agent, context, capability_summary=capability_summary
        )

    def _apply_cache_control_to_tools(self, tools: list[dict]) -> list[dict]:
        """Delegate to ToolExecutor (facade kept for internal callers)."""
        return self._tool_executor.apply_cache_control_to_tools(tools)

    async def _handle_system_capability(
        self,
        step: PlanStep,
        plan: PlanOutput,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Delegate system.* capability execution to SystemCapabilityHandler."""
        return await self._system_capability_handler.handle_system_capability(
            step, plan, user_id, workspace_id
        )

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
        """Delegate to AgentInvoker (facade kept for internal callers)."""
        return await self._invoker.call_agent(
            agent_name,
            message,
            user_id,
            trace=trace,
            max_tool_rounds=max_tool_rounds,
            workspace_id=workspace_id,
            capability_summary=capability_summary,
            tools_override=tools_override,
        )

    async def _call_composite_tool(
        self, tool_name: str, tool_input: dict, user_id: str = "", workspace_id: str = ""
    ) -> dict:
        """Delegate to ToolExecutor (facade kept for internal callers)."""
        return await self._tool_executor.call_composite_tool(
            tool_name, tool_input, user_id=user_id, workspace_id=workspace_id
        )

    async def _call_internal_tool(
        self, tool_name: str, tool_input: dict, server_prefix: str
    ) -> dict:
        """Delegate to ToolExecutor (facade kept for internal callers)."""
        return await self._tool_executor.call_internal_tool(tool_name, tool_input, server_prefix)

    async def _execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
        """Delegate to ToolExecutor (facade kept for internal callers + agent_loop)."""
        return await self._tool_executor.execute_tool(
            tool_name, tool_input, user_id, workspace_id=workspace_id
        )
