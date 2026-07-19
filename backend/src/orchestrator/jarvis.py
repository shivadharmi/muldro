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
from src.contracts import PlanOutput, PlanStep
from src.errors import (
    classify,
    new_correlation_id,
)
from src.middleware.observability import get_correlation_id
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import AGENTS, SubAgent, build_agent_set
from src.orchestrator.budget import BudgetTracker
from src.orchestrator.chat_processor import ChatProcessor
from src.orchestrator.connector_poller import ConnectorPoller
from src.orchestrator.context_assembler import ContextAssembler
from src.orchestrator.core_events import CoreEvent
from src.orchestrator.event_publisher import EventPublisher
from src.orchestrator.perception_runner import PerceptionRunner
from src.orchestrator.plan_store import PlanStore
from src.orchestrator.services import ServiceContainer
from src.orchestrator.surface_pusher import SurfacePusher
from src.orchestrator.system_capability_handler import SystemCapabilityHandler
from src.orchestrator.tool_executor import ToolExecutor
from src.orchestrator.tracing import TraceManager
from src.services.agent_registry import AgentRegistry
from src.services.interaction_learner import InteractionLearner
from src.services.trace_store import TraceStore

logger = logging.getLogger(__name__)

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
        checkpointer_provider=None,
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
        # checkpointer_provider: zero-arg callable → durable LangGraph checkpointer
        # (Step 6A.5); None/default falls back to MemorySaver inside the invoker.
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
            checkpointer_provider=checkpointer_provider,
        )
        # ConnectorPoller owns connector polling, raw-event ingest, and cursor
        # I/O — the connector-facing half of each perception cycle.
        self._poller = ConnectorPoller(
            settings,
            services,
            _db_factory_provider,
            self._events,
        )
        # PerceptionRunner owns the autonomous perception + synthesis loop. It
        # depends downward on the poller, invoker, events, surfaces, plans and
        # system capability handler — never on the chat path — which keeps the
        # chat<->perception relationship acyclic.
        self._perception = PerceptionRunner(
            settings,
            self._client,
            self._budget,
            self._trace_manager,
            _db_factory_provider,
            self._poller,
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

        # ChatProcessor owns the user-facing chat pipeline (intent → plan → route
        # → execute → present → surface → learn). Constructed last so all of its
        # collaborators — including the interaction learner and haiku model set
        # above — already exist. Depends downward on the invoker, context, plans,
        # surfaces, events, system-capability handler and perception runner; the
        # orchestrator keeps thin facades delegating to it.
        self._chat = ChatProcessor(
            settings,
            self._client,
            self._haiku_model,
            self._trace_manager,
            _db_factory_provider,
            self._invoker,
            self._context,
            self._plans,
            self._surfaces,
            self._events,
            self._system_capability_handler,
            self._perception,
            self._spawn_background,
            self._ensure_learner_deps,
            self._interaction_learner,
        )

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
        """Delegate to ChatProcessor (facade kept for internal callers)."""
        return await self._chat._get_available_capabilities(workspace_id)

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
        permission_mode: str = "auto",
    ) -> dict:
        """Facade -> ChatProcessor.process_message (batch chat entry point)."""
        return await self._chat.process_message(
            message,
            user_id,
            workspace_id,
            conversation_id=conversation_id,
            surface=surface,
            context=context,
            mode=mode,
            permission_mode=permission_mode,
        )

    def process_message_events(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        surface: str = "web",
        mode: str = "ask",
        context: dict | None = None,
        conversation_id: str | None = None,
        permission_mode: str = "auto",
    ) -> AsyncGenerator[CoreEvent, None]:
        """Facade -> ChatProcessor.process_message_events (typed-event chat path)."""
        return self._chat.process_message_events(
            message,
            user_id,
            workspace_id,
            surface=surface,
            mode=mode,
            context=context,
            conversation_id=conversation_id,
            permission_mode=permission_mode,
        )

    def process_message_stream(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        surface: str = "web",
        mode: str = "ask",
        context: dict | None = None,
        conversation_id: str | None = None,
        permission_mode: str = "auto",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Facade -> ChatProcessor.process_message_stream (SSE chat path)."""
        return self._chat.process_message_stream(
            message,
            user_id,
            workspace_id,
            surface=surface,
            mode=mode,
            context=context,
            conversation_id=conversation_id,
            permission_mode=permission_mode,
        )

    def resume_message_events(
        self,
        *,
        approval_id: str,
        decision: str,
        reason: str | None = None,
        user_id: str,
        workspace_id: str,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[CoreEvent, None]:
        """Facade -> ChatProcessor.resume_message_events (paused-turn RESUME, P2.4)."""
        return self._chat.resume_message_events(
            approval_id=approval_id,
            decision=decision,
            reason=reason,
            user_id=user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
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
        """Facade → ConnectorPoller.poll."""
        return await self._poller.poll(source, user_id, workspace_id)

    @staticmethod
    def _build_cursor_upsert_stmt(
        source: str,
        user_id: str,
        workspace_id: str,
        new_cursor: str,
        cursor_type: str,
    ):
        """Facade → ConnectorPoller.build_cursor_upsert_stmt (staticmethod)."""
        return ConnectorPoller.build_cursor_upsert_stmt(
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
        """Facade → ConnectorPoller.ingest_raw_events."""
        return await self._poller.ingest_raw_events(
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
        """Facade → ConnectorPoller.update_cursor."""
        await self._poller.update_cursor(source, user_id, workspace_id, new_cursor, cursor_type)

    async def generate_briefing(self, user_id: str, workspace_id: str = "") -> dict:
        """Generate the daily briefing through the Presenter agent.

        Uses the get_briefing tool to fetch real data from the intelligence
        backend (events, plans, approvals, goals) and then formats it through
        the Presenter agent for user-facing delivery.
        """
        trace = self._trace_manager.start_trace("scheduled_briefing")
        try:
            # Per-day idempotency, CHECK-BEFORE-GENERATE: if a briefing row
            # already exists for today, an earlier run already generated AND
            # delivered it. Short-circuit HERE — before get_briefing, the
            # Presenter LLM reformat, and the Presenter agent's own
            # push_ui_update (which ships the surface to the UI). Checking AFTER
            # generation (the old bug) only suppressed the secondary notify/push;
            # the user still saw the briefing regenerated and re-pushed every
            # tick. The (user_id, briefing_date) row is the idempotency key.
            #
            # INVARIANT: a wide check-to-write gap remains on the FIRST run of the
            # day (this read sees no row; the Briefing row is only written later
            # mid-run by the get_briefing tool below). This is safe ONLY because
            # the SchedulerLoop fires briefing schedules serially on a single
            # instance. The briefings table has a NON-unique index on
            # (user_id, briefing_date), so the DB will NOT stop a double-insert.
            # Before this can run multi-instance (or via skip_locked parallel
            # dispatch), add a UNIQUE constraint on briefings(user_id,
            # briefing_date) to enforce idempotency at the DB layer.
            if await self._briefing_already_exists(user_id, workspace_id):
                logger.info(
                    "Briefing already delivered today for %s — skipping regeneration",
                    user_id,
                )
                return {
                    "status": "skipped",
                    "reason": "already_delivered",
                    "trace_id": trace.trace_id,
                }

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

            # B3: Deliver briefing to user via notifications + workspace surface.
            # Single owner of delivery (the Presenter no longer notifies). The
            # already-delivered case returned early above, so this is the
            # first-of-day delivery: exactly one notification + one surface.
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

    async def _briefing_already_exists(self, user_id: str, workspace_id: str) -> bool:
        """Return True if a briefing row already exists for (user, today).

        Used as the per-day delivery idempotency key in generate_briefing so a
        scheduler re-fire does not re-notify / re-push.
        """
        from datetime import date as _date

        from sqlalchemy import select as _select

        from src.models.briefings import Briefing

        try:
            async with self._db_factory() as db:
                result = await db.execute(
                    _select(Briefing.briefing_id).where(
                        Briefing.user_id == user_id,
                        Briefing.briefing_date == _date.today(),
                    )
                )
                return result.scalar_one_or_none() is not None
        except Exception:
            # Fail open on the idempotency check: if we cannot tell, prefer
            # delivering (a missed briefing is worse than a rare duplicate).
            logger.debug("Briefing idempotency check failed", exc_info=True)
            return False

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
