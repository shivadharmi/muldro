"""ChatProcessor — the user-facing chat orchestration pipeline.

Extracted from ``MuldroOrchestrator`` (god-object decomposition, 2026-06-19). This
is the final and most delicate extraction: the chat path. It owns the single
intent → plan → route → execute → present → surface → learn sequence that drives
every interactive (chat) turn, in both the batch (``process_message``) and
streaming (``process_message_events`` / ``process_message_stream``) shapes.

Depends downward on AgentInvoker (running sub-agents), ContextAssembler
(conversation history), PlanStore (plan + interaction persistence), SurfacePusher
(A2UI surfaces), EventPublisher (runtime events), the SystemCapabilityHandler,
and the PerceptionRunner (only for the intent-driven perception bump) — never the
reverse, which keeps the chat<->perception relationship acyclic.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from src.config.settings import Settings
from src.connectors.mcp_bridge import close_turn_sessions
from src.contracts import PlanOutput
from src.deep_runtime.confirmation import (
    ABSENT,
    PRESENT,
    Presence,
    resolve_effective_permission_mode,
)
from src.errors import classify, new_correlation_id
from src.integrations.capabilities import CAPABILITY_CATALOG
from src.integrations.turn_scope import turn_scope
from src.middleware.observability import get_correlation_id
from src.orchestrator.chat_pipeline import resolve_plan_routing
from src.orchestrator.chat_single_lead import _ChatSingleLeadMixin
from src.orchestrator.core_events import (
    CoreEvent,
    IntentClassified,
    InteractionLogged,
    PlanReady,
    Presentation,
    RunCompleted,
    RunFailed,
    SystemStepResult,
    TraceStarted,
    UserActionsReady,
    ValidationFailed,
    agent_event_from_sse,
    core_event_to_sse,
)
from src.orchestrator.intent_classifier import (
    FAST_INTENTS,
    INTENT_CONFIDENCE_THRESHOLD,
    classify_intent,
    extract_plan,
    intent_to_plan,
)
from src.services.capability_resolver import CapabilityResolver
from src.services.workspace_entitlements import workspace_allows_bypass

logger = logging.getLogger(__name__)

# Per-message planner JSON contract suffix (mirrors PLANNER_PROMPT_V2's
# <final_response_contract>; kept near the user message as a reminder so the
# final response parser doesn't get tripped by stray prose).
_PLANNER_JSON_CONTRACT_SUFFIX = (
    "\n\nRespond with a single PlanOutput JSON object — no prose, "
    "no preamble, no code fences. Start with { and end with }."
)

# Uncataloged capabilities that the fast path (intent_to_plan) legitimately emits — these are
# reads/respond/reason, never external writes. Keep in sync with intent_to_plan's emissions
# (a regression test asserts every fast intent stays within these + cataloged reads).
_FAST_SAFE_CAPABILITIES = frozenset(
    {"respond", "reason", "perceive", "knowledge.search", "system.respond", "none"}
)


def _fast_step_is_write(capability: str) -> bool:
    """Fail-closed write classifier for the ungated fast path.

    A fast-path step is treated as a WRITE (→ divert to the gated Planner path) unless it is a
    known-safe fast capability (respond/reason/perceive/knowledge.search/...) or a cataloged
    read-only capability. An UNKNOWN capability fails CLOSED (treated as a write) so a future
    mutating fast intent can never execute ungated on the inline path.
    """
    if capability in _FAST_SAFE_CAPABILITIES:
        return False
    meta = CAPABILITY_CATALOG.get(capability)
    if meta is not None:
        return not meta.read_only  # cataloged: email.send -> write, email.list -> read
    return True  # unknown capability -> fail closed -> route through the gate + lock


class ChatProcessor(_ChatSingleLeadMixin):
    """Runs the conversational (chat) orchestration pipeline for the orchestrator.

    Inherits the deep single-lead permission path (run + resume + shared completion
    tail) from :class:`_ChatSingleLeadMixin` (P2.2c split-via-inheritance).
    """

    def __init__(
        self,
        settings: Settings,
        client,
        haiku_model: str,
        trace_manager,
        db_factory_provider,
        invoker,
        context,
        plans,
        surfaces,
        events,
        system_capability_handler,
        perception,
        spawn_background,
        ensure_learner_deps,
        interaction_learner,
    ):
        self._settings = settings
        self._client = client
        self._haiku_model = haiku_model
        self._trace_manager = trace_manager
        # Provider (not a captured value) so reassigning db_factory on the
        # orchestrator propagates to this collaborator.
        self._db_factory_provider = db_factory_provider
        self._invoker = invoker
        self._context = context
        self._plans = plans
        self._surfaces = surfaces
        self._events = events
        self._system_capability_handler = system_capability_handler
        self._perception = perception
        # Injected bound methods from the orchestrator (lifecycle owners).
        self._spawn_background = spawn_background
        self._ensure_learner_deps = ensure_learner_deps
        self._interaction_learner = interaction_learner

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

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
        """Process a user message and return the batch ``result`` dict.

        Accumulating adapter over :meth:`_process_core`: validate inputs (the
        batch-shaped ``{"error": ...}``), drive the core to exhaustion, and fold
        its ``CoreEvent``s into the result dict that ``routes_ws`` returns
        verbatim.

        ``mode`` defaults to ``"plan"``, which now only marks the plan
        ``requires_user_input`` — it no longer decides whether a risky step runs. What
        keeps this non-interactive entry safe is ``presence=ABSENT`` below: a write the
        gate would confirm is PREPARED for review instead of executed or interrupted.
        Interactive callers (WS surface actions, where the user's click is
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
            permission_mode=permission_mode,
            context=context,
            conversation_id=conversation_id,
            # BATCH entry: no synchronous user is on this turn, so a CONFIRM verdict cannot be
            # answered. `absent` therefore PREPARES such a write — recorded in full for review,
            # the turn finishing everything else — instead of interrupting into a void.
            presence=ABSENT,
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
                    #
                    # The per-step arms (``StepResult`` -> ``step_{i}_{cap}``, ``StepError``
                    # -> ``error_{step_id}``, ``PlanModeStepSkipped`` -> ``plan_ready``) went
                    # with the legacy arm: nothing yields those events any more, because
                    # there are no per-step agent calls to yield them. The lead's reply
                    # arrives as ``presentation`` on every non-failing turn.
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
        permission_mode: str = "auto",
    ) -> AsyncGenerator[CoreEvent, None]:
        """Public typed-event entry point for the conversational (streaming) path.

        Validate inputs (yielding a typed :class:`ValidationFailed`), then drive
        :meth:`_process_core`. Consumers that want SSE dicts use
        :meth:`process_message_stream`; consumers that fold typed events
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
            permission_mode=permission_mode,
            context=context,
            conversation_id=conversation_id,
            # STREAMING entry: a synchronous user IS on this turn, so a CONFIRM verdict can be
            # answered by interrupting. `process_message_stream` reaches `_process_core` through
            # this method, so it inherits `presence="present"` too.
            presence=PRESENT,
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
        permission_mode: str = "auto",
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
            permission_mode=permission_mode,
        ):
            sse = core_event_to_sse(event)
            if sse is not None:
                yield sse

    def _resolve_effective_presence(
        self, presence: str, workspace_id: str, user_id: str
    ) -> Presence:
        """Resolve the EFFECTIVE presence for this turn — a fail-safe downgrade only.

        A pause is only worth taking if it can be RESUMED, which needs a durable checkpointer.
        Without one, a nominally ``present`` turn is treated as ``absent`` so its gated writes
        are handled the safe way rather than interrupted into a thread nothing can re-enter.
        This subsumes the old ``needs_durable`` fallback exactly.
        """
        if presence == PRESENT and self._invoker.has_durable_checkpointer():
            return PRESENT
        if presence == PRESENT:
            logger.warning(
                "no durable checkpointer for workspace=%s user=%s — a pause could not be "
                "resumed; treating this turn as absent so gated writes are not interrupted "
                "into an unresumable thread",
                workspace_id,
                user_id,
            )
        return ABSENT

    async def _resolve_effective_mode(
        self, permission_mode: str, presence: str, workspace_id: str
    ) -> str:
        """Resolve the EFFECTIVE permission mode for this chat turn.

        SECURITY — this decides whether/how a write is gated. The policy itself is the pure,
        exhaustively-tested :func:`resolve_effective_permission_mode`; this method supplies the
        one input that needs a DB read (the workspace bypass entitlement).

        ``presence`` must ALREADY be the effective presence
        (see :meth:`_resolve_effective_presence`) and may only ever DOWNGRADE authority —
        there is no branch by which it grants what ``permission_mode`` did not.

        ALWAYS returns a mode: every turn runs one lead, so there is no "not applicable"
        answer and an unknown mode fails closed to ``ask`` rather than opting out. Do not
        reintroduce a transport flag (a feature toggle, a runtime name, a channel) as an
        authority input here — the two inputs above are the whole vocabulary.
        """
        bypass_entitled = True
        if permission_mode == "bypass":
            bypass_entitled = await workspace_allows_bypass(self._db_factory, workspace_id)
            if not bypass_entitled:
                logger.warning(
                    "workspace %s not entitled for bypass — downgrading to auto", workspace_id
                )
        return resolve_effective_permission_mode(
            permission_mode, presence, bypass_entitled=bypass_entitled
        )

    async def _process_core(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        *,
        surface: str,
        mode: str,
        context: dict | None,
        conversation_id: str | None,
        permission_mode: str = "auto",
        presence: str = ABSENT,
    ) -> AsyncGenerator[CoreEvent, None]:
        """Unified chat-orchestration pipeline shared by both public entry points.

        Drives the single intent → plan → lead → present → surface → learn sequence and
        yields typed ``CoreEvent``s. ``process_message_stream`` translates them to SSE;
        ``process_message`` folds them into the batch result dict. Assumes inputs are
        already validated by the calling adapter; owns the trace lifecycle and the
        terminal ``RunFailed`` on exception.

        ONE lead runs per turn, scoped to the plan's capability union, discovering its own
        tools. There is no per-step agent routing and no Presenter step — the lead's own
        reply is the turn's reply.

        Runtime events fire in the background (the stream path's discipline,
        adopted for both per chat-pipeline-fold drift #4).

        ``presence`` defaults to ``absent`` — the FAIL-SAFE direction. An unknown future caller
        gets the cautious treatment rather than interrupting into a void or reaching ``bypass``.
        """
        trace = self._trace_manager.start_trace("user_message")

        async with turn_scope(on_close=close_turn_sessions):

            def _fire_event(event_type: str, **kwargs: Any) -> None:
                self._spawn_background(self._events.emit_runtime_event(event_type, **kwargs))

            try:
                yield TraceStarted(trace_id=trace.trace_id)

                # Resolve the turn's EFFECTIVE presence ONCE, inside the guarded region, so both
                # `_resolve_effective_mode` call sites below share one answer and any failure
                # here is sanitised into RunFailed and still finishes the trace.
                presence = self._resolve_effective_presence(presence, workspace_id, user_id)

                _fire_event(
                    "command_received",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    payload={"surface": surface, "message_preview": message[:100]},
                )

                history_block = await self._context.load_conversation_history(
                    conversation_id, user_id=user_id, workspace_id=workspace_id
                )

                # P2.5c planless reroute: when the flag is on, DROP the Planner entirely — skip
                # classify_intent, the fast-path, the Planner call, the Plan record, PlanReady,
                # resolve_plan_routing, and UserActionsReady — and route the whole turn through
                # ONE connector-scoped lead (which self-plans + calls its own system.* tools).
                # `self._settings.chat_planless` (a cheap bool, OFF by default) short-circuits
                # FIRST so the flag-off path is byte-identical: this block is skipped and control
                # falls through to the classify_intent + Planner flow below. Both paths resolve
                # the effective permission mode through the SAME `_resolve_effective_mode`.
                if self._settings.chat_planless:
                    effective_mode = await self._resolve_effective_mode(
                        permission_mode, presence, workspace_id
                    )
                    async for evt in self._run_single_lead_planless(
                        message=message,
                        history_block=history_block,
                        trace=trace,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        effective_mode=effective_mode,
                        presence=presence,
                        conversation_id=conversation_id,
                    ):
                        yield evt
                    return

                # Step 0: Fast intent classification
                intent, confidence, sources = await classify_intent(
                    message, history_block, workspace_id
                )
                yield IntentClassified(intent=intent, confidence=confidence)

                if sources:
                    await self._perception._bump_perception_for_sources(
                        sources, user_id, workspace_id
                    )

                # Decide routing based on intent AND mode
                if mode in ("execute", "plan"):
                    use_planner = True
                else:
                    use_planner = (
                        intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD
                    )

                # Fast path: synthesize the lightweight plan and FAIL-CLOSED fence any write
                # (Step 6C). The fast path executes inline, UNGATED (skips the Planner AND
                # GraphExecutor's trust gate + write lock). If a fast intent ever emits a write
                # capability, divert to the Planner path so the write goes through the gate +
                # lock instead of the ungated inline loop.
                fast_plan: PlanOutput | None = None
                if not use_planner:
                    capabilities = await self._get_available_capabilities(workspace_id)
                    fast_plan = intent_to_plan(intent, message, capabilities)
                    write_caps = [
                        s.capability for s in fast_plan.steps if _fast_step_is_write(s.capability)
                    ]
                    if write_caps:
                        logger.warning(
                            "fast intent %s emitted write capability(ies) %s — diverting to "
                            "Planner (gate+lock)",
                            intent,
                            write_caps,
                        )
                        use_planner = True
                        fast_plan = None

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

                    async for evt in self._invoker.call_agent_stream(
                        "planner",
                        message=planner_message,
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                        presence=presence,
                    ):
                        yield agent_event_from_sse(evt)
                        if evt.get("event") == "agent_done":
                            plan_text = evt.get("text", "")

                    plan = extract_plan(plan_text)
                else:
                    plan = fast_plan  # already synthesized + write-fenced above

                # Apply mode overrides
                if mode == "plan":
                    plan = plan.model_copy(update={"requires_user_input": True})

                # Persist Plan record if multi-step or has write risk
                if len(plan.steps) > 1 or any(s.risk not in ("none",) for s in plan.steps):
                    import hashlib

                    goal_hash = hashlib.sha256((plan.goal or "").encode()).hexdigest()[:16]
                    plan = await self._plans.persist_plan_record(
                        plan,
                        user_id,
                        workspace_id,
                        idempotency_key=f"user:{goal_hash}",
                    )

                plan_dict = plan.model_dump(mode="json")

                ilog_id = await self._plans.log_interaction(
                    user_id,
                    workspace_id,
                    trace.trace_id,
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

                # Step 2: the steps the USER must act on (reported, never executed here).
                # Everything else is the lead's: it is built with the plan's capability union
                # and discovers its own tools, so nothing is pre-resolved per step.
                user_steps = resolve_plan_routing(plan.steps)

                # Step 3: resolve the EFFECTIVE permission mode (fail-safe downgrades only) and
                # run the turn's ONE lead. SECURITY — the mode decides whether/how a write is
                # gated; `_resolve_effective_mode` is the single resolution both this path and
                # the planless path above share.
                effective_mode = await self._resolve_effective_mode(
                    permission_mode, presence, workspace_id
                )

                # THE LEAD — delegated to the _ChatSingleLeadMixin (:meth:`_run_single_lead`):
                # system.* steps -> user actions -> build lead -> stream -> pause seam ->
                # re-home reply -> completion tail. It owns its own tail, so `_process_core`
                # has none of its own: on a pause the mixin emits ``ApprovalRequired`` and
                # stops WITHOUT the tail, and the resume path runs the tail on the terminal
                # reply instead.
                async for evt in self._run_single_lead(
                    plan=plan,
                    message=message,
                    history_block=history_block,
                    intent=intent,
                    trace=trace,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    effective_mode=effective_mode,
                    presence=presence,
                    user_steps=user_steps,
                ):
                    yield evt

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
