"""ChatProcessor — the user-facing chat orchestration pipeline.

Extracted from ``JarvisOrchestrator`` (god-object decomposition, 2026-06-19). This
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
from src.errors import classify, new_correlation_id
from src.integrations.capabilities import CAPABILITY_CATALOG
from src.integrations.turn_scope import turn_scope
from src.middleware.observability import get_correlation_id
from src.orchestrator.chat_pipeline import (
    build_presenter_message,
    build_user_action_block,
    format_prior_results_for_presenter,
    format_prior_step_results,
    resolve_plan_routing,
)
from src.orchestrator.core_events import (
    ApprovalRequired,
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
from src.orchestrator.divergence import ShadowDecision
from src.orchestrator.intent_classifier import (
    FAST_INTENTS,
    INTENT_CONFIDENCE_THRESHOLD,
    classify_intent,
    extract_plan,
    intent_to_plan,
)
from src.orchestrator.presenter_skip import extract_perceiver_synthesis, single_read_step
from src.services.capability_resolver import CapabilityResolver
from src.services.surface_mapping import extract_surface_spec, strip_surface_blocks
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


def _shadow_compare_enabled(settings) -> bool:
    """True only when ``settings.shadow_sample_rate`` is a real positive number.

    Several pre-existing orchestrator-construction test harnesses build ``settings``
    as a bare ``MagicMock()`` (predating this field), whose unconfigured attribute
    is not float-comparable — ``MagicMock() > 0`` raises ``TypeError``. Treat
    anything that isn't a real ``int``/``float`` as "off", matching the real
    ``Settings`` default (0.0) — never raises.
    """
    rate = getattr(settings, "shadow_sample_rate", 0.0)
    return isinstance(rate, (int, float)) and rate > 0


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


class ChatProcessor:
    """Runs the conversational (chat) orchestration pipeline for the orchestrator."""

    # Class-level default so instances built via ``__new__`` (some orchestrator test
    # harnesses bypass __init__) still resolve ``self._shadow_runner`` as None without
    # a getattr fallback — the shadow-compare spawn guard below reads it directly.
    _shadow_runner = None

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
        shadow_runner=None,
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
        self._shadow_runner = shadow_runner

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
            permission_mode=permission_mode,
            prompt_style="structured",
            context=context,
            conversation_id=conversation_id,
            # Batch/scheduled turns have NO synchronous user to confirm a pause, so the
            # single-lead permission path (incl. bypass) is never taken here — an
            # ask/auto pause would orphan the checkpoint and the ApprovalRequired event
            # is silently dropped by the batch fold below (case _: pass).
            can_pause=False,
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
        permission_mode: str = "auto",
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
            permission_mode=permission_mode,
            prompt_style="conversational",
            context=context,
            conversation_id=conversation_id,
            # Streaming entry: a synchronous user IS present to confirm a pause, so the
            # single-lead ask/auto path may suspend the turn for approval.
            # ``process_message_stream`` reaches ``_process_core`` through this method,
            # so it inherits ``can_pause=True`` too.
            can_pause=True,
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

    async def resume_message_events(
        self,
        *,
        approval_id: str,
        decision: str,
        reason: str | None = None,
        user_id: str,
        workspace_id: str,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[CoreEvent, None]:
        """Drive a paused chat single-lead turn's RESUME continuation (P2.2b, Corr-C1).

        The sibling of :meth:`_process_core` for the RESUME half of an ask/auto turn: when
        the action-time permission gate PAUSED for the user's confirmation, ``_process_core``
        yielded :class:`ApprovalRequired` and ``return``\\ ed — SKIPPING its completion tail
        (the turn wasn't done). The resume is a SEPARATE HTTP request. This wrapper re-enters
        the paused thread via :meth:`AgentInvoker.resume_deep_lead`, re-streams the
        continuation frames, and — critically — RE-HOMES the reply as a :class:`Presentation`
        and runs the completion tail (surface push + ``RunCompleted``). Without it the
        approved write fires but the reply is never persisted (``routes_chat`` persists only
        on a ``Presentation`` → empty bubble) and no A2UI surface builds — the exact C-CORR2
        failure P1 fixed for the initial turn, un-fixed on resume.

        Yields the SAME ``CoreEvent`` union :meth:`_process_core` yields, so the resume HTTP
        endpoint (a later task) reuses :func:`core_event_to_sse` verbatim. Mirrors
        ``_process_core``'s trace / ``turn_scope`` / try / except / finally shell.

        ``conversation_id`` is accepted for the resume endpoint's parity and the P2.7 learner
        (below); the reply + surface (the user-visible fixes) do not need it, so it is unused
        today.
        """
        trace = self._trace_manager.start_trace("resume_approval")

        async with turn_scope(on_close=close_turn_sessions):

            def _fire_event(event_type: str, **kwargs: Any) -> None:
                self._spawn_background(self._events.emit_runtime_event(event_type, **kwargs))

            presenter_text = ""
            try:
                yield TraceStarted(trace_id=trace.trace_id)

                async for frame in self._invoker.resume_deep_lead(
                    approval_id=approval_id,
                    decision=decision,
                    reason=reason,
                    user_id=user_id,
                    workspace_id=workspace_id,
                ):
                    if frame.get("event") == "error":
                        # resume_deep_lead REFUSED (invalid decision / tenant-A6 guard failure /
                        # not-resumable). The frame is ALREADY a client-safe SSE ``error`` dict
                        # (the invoker owns its message shape) — pass it through verbatim via the
                        # AgentStreamEvent fallback and STOP. No completion tail runs: nothing
                        # completed. ``finish_trace`` in the ``finally`` still fires.
                        yield agent_event_from_sse(frame)
                        return
                    if frame.get("event") == "approval_needed":
                        # CHAINED re-pause: a 2ND write in the resumed continuation paused again.
                        # Same suspend semantics as ``_process_core``'s pause seam — yield the
                        # typed pause event and ``return``, SKIPPING the tail (the turn is
                        # suspended again). The paused checkpoint stays live; the resume path
                        # re-enters on the NEXT decision. The typed ApprovalRequired REPLACES the
                        # raw passthrough so the frame is emitted EXACTLY ONCE.
                        yield ApprovalRequired(
                            approval_id=frame.get("approval_id"),
                            capability=frame.get("capability"),
                            risk_level=frame.get("risk_level"),
                            thread_id=frame.get("thread_id"),
                        )
                        return
                    yield agent_event_from_sse(frame)
                    if frame.get("event") == "agent_done":
                        # RE-HOME the presenter output (C-CORR2): resume_deep_lead emits NO
                        # Presentation frame, so synthesize it here — else the resumed reply is
                        # never persisted (routes_chat persists only on Presentation) and the
                        # chat bubble is empty. Keep presenter_text RAW for the tail's surface
                        # extraction.
                        presenter_text = frame.get("text", "")
                        yield Presentation(text=strip_surface_blocks(presenter_text))

                # COMPLETION TAIL (mirrors ``_process_core``'s completion tail: run_completed →
                # surface push → RunCompleted, MINUS the interaction-learner — see P2.7 below).
                # Runs ONLY on the terminal reply (the ``return``s above skip it for a suspended
                # / refused turn). Currently DUPLICATED with _process_core's tail — the shared
                # ``_emit_completion_tail`` extraction is the P2.7 structure-only cleanup.
                _fire_event(
                    "run_completed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=None,
                    payload={"trace_id": trace.trace_id},
                )

                # Push workspace surface (Presenter-driven). Keep presenter_text raw for
                # extraction — it still carries the fenced surface blocks.
                surface_id = None
                try:
                    surface_spec = extract_surface_spec(presenter_text)
                    if surface_spec and surface_spec.should_surface:
                        surface_id = await self._surfaces.push_presenter_surface(
                            spec=surface_spec,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            run_id=None,
                            response_text=presenter_text,
                        )
                except Exception:
                    logger.warning("Surface push failed", exc_info=True)

                # P2.7 (DEFERRED — not built here): the interaction-learner spawn would go here,
                # mirroring ``_process_core``'s tail. It needs the ORIGINAL user message
                # (``learn(user_message=..., agent_response=presenter_text)``), which is NOT
                # persisted on the Approval today (``context_block`` holds history + plan, not the
                # raw ask). Persisting ``user_message`` on the Approval (a gate thread-through) +
                # spawning the learner is a P2.7 enrichment. The reply + surface (the user-visible
                # Corr-C1 fixes) ship now; the learner (background enrichment) is deferred.
                #
                # P2.7 (structure-only): this tail DUPLICATES ``_process_core``'s tail
                # (run_completed + surface + RunCompleted). Extracting a shared
                # ``_emit_completion_tail`` helper (alongside the ``_stream_and_reap`` dedup) is a
                # P2.7 cleanup — NOT refactored here (chat_processor.py is the only file this task
                # touches; ``_process_core`` is left unchanged).

                yield RunCompleted(trace_id=trace.trace_id, run_id=None, surface_id=surface_id)

            except Exception as e:
                logger.error("resume_message_events failed: %s", e, exc_info=True)
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
        permission_mode: str = "auto",
        can_pause: bool = False,
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
                self._spawn_background(self._events.emit_runtime_event(event_type, **kwargs))

            try:
                yield TraceStarted(trace_id=trace.trace_id)

                _fire_event(
                    "command_received",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    payload={"surface": surface, "message_preview": message[:100]},
                )

                history_block = await self._context.load_conversation_history(
                    conversation_id, user_id=user_id
                )

                # Step 0: Fast intent classification
                intent, confidence, sources = await classify_intent(
                    self._client, self._haiku_model, message, history_block
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

                # Step 2: Pre-resolve routing and tools for all steps
                step_routing, user_steps = await resolve_plan_routing(
                    self._db_factory, workspace_id, plan.steps
                )

                # Step 3: Execute steps. `presenter_text` + `agent_name` are declared
                # before the branch so the SHARED TAIL below (surface push, learner,
                # shadow-compare) sees them on BOTH paths. The single-lead branch leaves
                # agent_name=None → the shadow guard (`agent_name is not None`) skips it,
                # matching the empty-routing case.
                presenter_text = ""
                agent_name: str | None = None

                # Step 10D P2.3 (chat permission model): resolve the EFFECTIVE permission
                # mode for the deep single-lead chat path, applying FAIL-SAFE downgrades.
                # SECURITY — this decides whether/how a write is gated.
                #
                # `self._settings.deep_single_lead` (a cheap bool, OFF in prod) is checked
                # FIRST so the default legacy path does ZERO extra work — no
                # effective_chat_runtime() Redis round-trip, no entitlement / checkpointer
                # read — keeping it byte-identical. `can_pause` is False on the batch /
                # scheduled path (no synchronous user to confirm a pause), so the whole
                # single-lead path (INCLUDING bypass) is only ever taken on the streaming
                # entries. `permission_mode` is an INDEPENDENT field, never derived from
                # the legacy `mode` slot.
                #
                # Downgrades are fail-safe ONLY (never escalate authority):
                #   * bypass on a workspace that has not opted in  → auto
                #   * ask/auto with no durable checkpointer to resume a pause → legacy
                effective_mode: str | None = None
                if (
                    self._settings.deep_single_lead
                    and can_pause
                    and permission_mode in ("bypass", "ask", "auto")
                    and await self._invoker.effective_chat_runtime() == "deep"
                ):
                    effective_mode = permission_mode
                    if effective_mode == "bypass" and not await workspace_allows_bypass(
                        self._db_factory, workspace_id
                    ):
                        logger.warning(
                            "workspace %s not entitled for bypass — downgrading to auto",
                            workspace_id,
                        )
                        effective_mode = "auto"
                    needs_durable = effective_mode in ("ask", "auto")
                    if needs_durable and not self._invoker.has_durable_checkpointer():
                        logger.warning(
                            "no durable checkpointer — chat permission gate cannot resume a "
                            "pause; falling back to the legacy path"
                        )
                        effective_mode = None

                # The deep single-lead chat path (P2.3): taken for the resolved effective mode
                # (bypass/ask/auto). Same safety posture as today's ungated chat, plus the
                # action-time permission gate (ask/auto) that suspends a write for confirmation.
                if effective_mode in ("bypass", "ask", "auto"):
                    # SINGLE-LEAD PATH. The Planner already ran → the plan carries the
                    # plan-union scope + the system.* steps. Same safety posture as today's
                    # ungated chat, just single-lead execution (one lead vs N per-step calls
                    # + presenter).
                    # (a) system.* steps run deterministically here (Planner-produced;
                    # handle_system_capability takes only (step, plan, ...) — no data dep on
                    # agent-step outputs — so running them before the lead is behavior-
                    # equivalent to the per-step loop).
                    for step in plan.steps:
                        if getattr(step, "actor", None) != "user" and step.capability.startswith(
                            "system."
                        ):
                            sys_result = (
                                await self._system_capability_handler.handle_system_capability(
                                    step, plan, user_id, workspace_id
                                )
                            )
                            yield SystemStepResult(
                                key=f"system_{step.capability}", output=sys_result
                            )
                    # (b) user actions (same contract as the legacy path).
                    if user_steps:
                        yield UserActionsReady(
                            steps=[
                                {"description": s.description, "context": s.user_context}
                                for s in user_steps
                            ]
                        )
                    # (c) build the lead (plan-union scope) + assemble its ambient context,
                    # stream it, and RE-HOME the reply. The RAW user `message` is the human
                    # turn; history + plan summary go into the system `context_block`.
                    lead = await self._invoker.build_chat_lead(plan.steps, workspace_id)
                    lead_ctx = await self._context.assemble_context(
                        "lead", message, user_id=user_id, workspace_id=workspace_id
                    )
                    parts = [p for p in (lead_ctx, history_block) if p]
                    if plan.goal or plan.reasoning:
                        parts.append(f"[Plan]\nGoal: {plan.goal}\nReasoning: {plan.reasoning}")
                    context_block = "\n\n".join(parts)
                    async for frame in self._invoker.stream_deep_lead(
                        lead,
                        message=message,
                        context_block=context_block,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        intent=intent,
                        trace=trace,
                        permission_mode=effective_mode,
                    ):
                        # PAUSE SEAM (P2.3): the action-time permission gate paused this
                        # turn for the user's confirmation. Emit the typed pause event and
                        # `return` — ending the generator SKIPS the shared completion tail
                        # (run_completed / surface / learner / RunCompleted) for a suspended
                        # turn, while the `finally` below still finishes the trace and
                        # turn_scope still tears down the MCP sessions. The paused deep
                        # checkpoint stays live; the resume path (later task) re-enters the
                        # thread and runs the tail on the terminal reply. The typed
                        # ApprovalRequired REPLACES the raw agent_event_from_sse passthrough
                        # so the frame is emitted EXACTLY ONCE.
                        #
                        # Abandoning the half-consumed stream_deep_lead generator here is
                        # INTENTIONAL and safe (unlike the drain-the-generator pattern at the
                        # plan step above): stream_deep_agent_events has ALREADY returned after
                        # the approval_needed frame (nothing left to drain), and stream_deep_lead
                        # set its own paused=True before yielding, so its `if not paused:
                        # reap_thread` never runs — the paused checkpoint is preserved for resume.
                        if frame.get("event") == "approval_needed":
                            yield ApprovalRequired(
                                approval_id=frame.get("approval_id"),
                                capability=frame.get("capability"),
                                risk_level=frame.get("risk_level"),
                                thread_id=frame.get("thread_id"),
                            )
                            return
                        yield agent_event_from_sse(frame)
                        if frame.get("event") == "agent_done":
                            presenter_text = frame.get("text", "")
                            # RE-HOME the presenter output (C-CORR2): stream_deep_lead emits
                            # NO Presentation frame, so synthesize it here — else the reply is
                            # never persisted (routes_chat persists only on Presentation) and
                            # the chat bubble is empty. Keep presenter_text RAW for the shared
                            # tail's surface extraction.
                            yield Presentation(text=strip_surface_blocks(presenter_text))
                else:
                    # LEGACY per-step path — the existing body, moved UNCHANGED (indented one
                    # level). `step_outputs` is the narrow prior-context accumulator (agent
                    # step text only) injected into downstream agents — kept separate from
                    # the batch result contract so plan/trace metadata never leaks into agent
                    # prompts (drift #2).
                    step_outputs: dict[str, str] = {}
                    for step_idx, (step, agent_name, tools) in enumerate(step_routing):
                        if step.capability.startswith("system."):
                            sys_result = (
                                await self._system_capability_handler.handle_system_capability(
                                    step, plan, user_id, workspace_id
                                )
                            )
                            yield SystemStepResult(
                                key=f"system_{step.capability}", output=sys_result
                            )
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
                        async for evt in self._invoker.call_agent_stream(
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
                            (k for k in step_outputs if k.endswith(f"_{read_step.capability}")),
                            None,
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
                        async for evt in self._invoker.call_agent_stream(
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
                        surface_id = await self._surfaces.push_presenter_surface(
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

                # Shadow-compare (Step 10B Task 3b, GUARDED for byte-neutrality): with the
                # default shadow_sample_rate=0.0 this branch never runs, so NO extra
                # background task is ever scheduled — the live path is byte-identical to
                # before this wiring existed. write_intents=frozenset() on the
                # authoritative side is a documented Phase-3 limitation: authoritative
                # tool-intent capture (mirroring what _IntentRecordingShadowExecutor does
                # for the shadow side) is a 10D enrichment, consistent with the plan's
                # "no live verification-FN signal" scoping.
                #
                # The class-level ``_shadow_runner = None`` default lets ``__new__``-built
                # harnesses read the attribute directly (no getattr). ``_shadow_compare_enabled``
                # (rather than a direct ``> 0``) is still needed: several pre-existing harnesses
                # build ``settings`` as a bare ``MagicMock()`` (predating this field), whose
                # unconfigured rate attribute is not float-comparable — it must degrade to "off",
                # never raise.
                if (
                    self._shadow_runner is not None
                    and _shadow_compare_enabled(self._settings)
                    and agent_name is not None
                ):
                    auth_decision = ShadowDecision(
                        route=agent_name, final_text=presenter_text, write_intents=frozenset()
                    )
                    self._spawn_background(
                        self._shadow_runner.maybe_run_shadow(
                            agent_name=agent_name,
                            message=message,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            authoritative_decision=auth_decision,
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
