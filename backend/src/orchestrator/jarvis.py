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
from src.orchestrator.perception_runner import PerceptionRunner
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


# _fetch_thread_contexts now lives in perception_runner.py (its only consumer).


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
        # PerceptionRunner owns the autonomous perception + synthesis loop. It
        # depends downward on the invoker, events, surfaces, plans and system
        # capability handler — never on the chat path — which keeps the
        # chat<->perception relationship acyclic.
        self._perception = PerceptionRunner(
            settings,
            self._client,
            services,
            self._budget,
            self._trace_manager,
            _db_factory_provider,
            self._invoker,
            self._events,
            self._surfaces,
            self._plans,
            self._system_capability_handler,
            self._spawn_background,
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
        """Facade → PerceptionRunner.run_cross_source_synthesis."""
        return await self._perception.run_cross_source_synthesis(
            source_names, user_id, workspace_id
        )

    async def run_perception_cycle(self, source: str, user_id: str, workspace_id: str = "") -> dict:
        """Facade → PerceptionRunner.run_perception_cycle."""
        return await self._perception.run_perception_cycle(source, user_id, workspace_id)

    async def _poll_connector(
        self, source: str, user_id: str, workspace_id: str
    ) -> tuple[list, str | None, str | None, str]:
        """Facade → PerceptionRunner._poll_connector."""
        return await self._perception._poll_connector(source, user_id, workspace_id)

    @staticmethod
    def _build_cursor_upsert_stmt(
        source: str,
        user_id: str,
        workspace_id: str,
        new_cursor: str,
        cursor_type: str,
    ):
        """Facade → PerceptionRunner._build_cursor_upsert_stmt (staticmethod)."""
        return PerceptionRunner._build_cursor_upsert_stmt(
            source, user_id, workspace_id, new_cursor, cursor_type
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
        """Facade → PerceptionRunner._ingest_raw_events."""
        return await self._perception._ingest_raw_events(
            raw_events,
            user_id,
            workspace_id,
            source=source,
            new_cursor=new_cursor,
            cursor_type=cursor_type,
        )

    async def _update_cursor(
        self,
        source: str,
        user_id: str,
        workspace_id: str,
        new_cursor: str | None,
        cursor_type: str = "opaque",
    ) -> None:
        """Facade → PerceptionRunner._update_cursor."""
        await self._perception._update_cursor(
            source, user_id, workspace_id, new_cursor, cursor_type
        )

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
        """Facade → PerceptionRunner._apply_perception_policy_from_planner."""
        await self._perception._apply_perception_policy_from_planner(
            planner_text, source, user_id, workspace_id, event_count
        )

    async def _queue_perception_plan(
        self,
        planner_result: str,
        source: str,
        user_id: str,
        workspace_id: str,
        trace_id: str,
    ) -> PlanOutput | None:
        """Facade → PerceptionRunner._queue_perception_plan."""
        return await self._perception._queue_perception_plan(
            planner_result, source, user_id, workspace_id, trace_id
        )

    async def _bump_perception_for_sources(
        self, sources: list[str], user_id: str, workspace_id: str
    ) -> None:
        """Facade → PerceptionRunner._bump_perception_for_sources."""
        await self._perception._bump_perception_for_sources(sources, user_id, workspace_id)

    @staticmethod
    def _extract_perception_policy(planner_text: str):
        """Facade → PerceptionRunner._extract_perception_policy (staticmethod)."""
        return PerceptionRunner._extract_perception_policy(planner_text)

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
