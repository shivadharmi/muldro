"""The chat lead — how every chat turn actually runs.

Extracted from :class:`~src.orchestrator.chat_processor.ChatProcessor` (P2.2c,
structure-only) so ``chat_processor.py`` stays under the file-size cap. ``_process_core``
does intent → plan; everything after that is here. One lead per turn, scoped to the
plan's capability union, discovering its own tools — there is no per-step agent routing
and no Presenter step:

* :meth:`_run_single_lead` — the PLANNED path (system.* steps → user actions → build lead
  → stream → pause seam → re-home the reply → completion tail).
* :meth:`_run_single_lead_planless` — the same, without a plan (``chat_planless``).
* :meth:`resume_message_events` — the RESUME half of a paused ask/auto turn (drives the
  invoker's ``resume_deep_lead``, re-homes the continuation reply, runs the tail).
* :meth:`_emit_completion_tail` — the ONE completion tail (run_completed → surface push →
  optional learner → ``RunCompleted``) shared by every producer of a terminal reply.

INVARIANT: a non-failing turn ALWAYS ends in a ``Presentation``. ``routes_chat`` persists
the reply only on that event and the frontend has no other source of terminal text, so a
dropped ``Presentation`` is an empty chat bubble — a silent failure, not an error.

``ChatProcessor`` inherits this mixin (split-via-inheritance) — the methods run on a full
``ChatProcessor`` instance and reach its collaborators via ``self`` exactly as before.
No behavior changes: the methods moved verbatim (the tail folded into one helper).
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

from src.connectors.mcp_bridge import close_turn_sessions
from src.deep_runtime.authorization import AuthorizationSource
from src.errors import _GENERIC_CODE, _GENERIC_MESSAGE, classify, new_correlation_id
from src.integrations.turn_scope import turn_scope
from src.middleware.observability import get_correlation_id
from src.orchestrator.core_events import (
    ApprovalRequired,
    CoreEvent,
    InteractionLogged,
    Presentation,
    RunCompleted,
    RunFailed,
    SystemStepResult,
    TraceStarted,
    UserActionsReady,
    agent_event_from_sse,
)
from src.services.surface_mapping import extract_surface_spec, strip_surface_blocks

logger = logging.getLogger(__name__)

# What a turn says when its lead finished but wrote nothing. An EMPTY reply is not the same
# as a short one: ``routes_chat`` gates the assistant-message insert on truthy reply text, so
# an empty ``Presentation`` persists nothing and the turn has no answer at all when the
# conversation is reloaded. This is the honest alternative — a plain statement of what
# happened, never a stand-in for a reply the lead did not write.
_NO_REPLY_TEXT = "I finished this turn without producing a reply."


class _ChatSingleLeadMixin:
    """The deep single-lead chat path (run + resume) + the shared completion tail.

    Mixed into :class:`~src.orchestrator.chat_processor.ChatProcessor`; every attribute
    referenced (``self._invoker``, ``self._surfaces``, ``self._events``,
    ``self._spawn_background`` …) is provided by ``ChatProcessor.__init__``.
    """

    async def _emit_completion_tail(
        self,
        *,
        trace,
        presenter_text: str,
        user_id: str,
        workspace_id: str,
        message: str | None = None,
        intent: str | None = None,
        run_learner: bool = False,
    ) -> AsyncGenerator[CoreEvent, None]:
        """The ONE completion tail: run_completed event → surface push → optional
        learner spawn → terminal ``RunCompleted``.

        Shared by all producers of a terminal chat reply so the tail (and its exact
        ordering) lives in one place. ``resume_message_events`` sets ``run_learner=True``
        (A1: fires the learner on an approved resume; ``message`` = the ORIGINAL user
        message, persisted on the Approval and read back off the ``agent_done`` frame).

        Ordering is preserved: run_completed → surface → learner → ``RunCompleted``.
        """
        self._spawn_background(
            self._events.emit_runtime_event(
                "run_completed",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=None,
                payload={"trace_id": trace.trace_id},
            )
        )

        # Push workspace surface. DEPRECATED path: nothing asks the lead for a fenced
        # surface block any more, so this parser exists only to catch a model that still
        # emits the old shape from habit. It logs when it fires, so the fallback's usage
        # is visible rather than silent. ``presenter_text`` is kept raw for extraction.
        surface_id = None
        try:
            surface_spec = extract_surface_spec(presenter_text)
            if surface_spec is not None:
                logger.warning(
                    "lead emitted a legacy fenced surface block; the fenced path is "
                    "deprecated and unvalidated"
                )
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

        # Interaction learning (async, non-blocking). Skipped on resume (P2.7).
        if run_learner and self._interaction_learner:
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

    async def _run_single_lead(
        self,
        *,
        plan,
        message: str,
        history_block: str,
        intent: str,
        trace,
        user_id: str,
        workspace_id: str,
        effective_mode: str,
        presence: str,
        user_steps,
        authorization_source: str = AuthorizationSource.DIRECT_USER_REQUEST,
    ) -> AsyncGenerator[CoreEvent, None]:
        """Run the turn's ONE lead over a plan, for the already-resolved effective mode
        (bypass/ask/auto) — plus the action-time permission gate (ask/auto) that suspends
        or prepares a write depending on ``presence``.

        The Planner already ran → the plan carries the plan-union scope + the system.*
        steps. On a normal reply this runs its own completion tail; on a pause it emits
        ``ApprovalRequired`` and returns WITHOUT the tail (the resume path runs the tail
        on the terminal reply).
        """
        # (a) system.* steps run deterministically here (Planner-produced;
        # handle_system_capability takes only (step, plan, ...) — no data dep on the
        # lead's output — so running them before the lead is well-defined).
        for step in plan.steps:
            if getattr(step, "actor", None) != "user" and step.capability.startswith("system."):
                sys_result = await self._system_capability_handler.handle_system_capability(
                    step, plan, user_id, workspace_id
                )
                yield SystemStepResult(key=f"system_{step.capability}", output=sys_result)
        # (b) user actions — reported to the user, never executed here.
        if user_steps:
            yield UserActionsReady(
                steps=[
                    {"description": s.description, "context": s.user_context} for s in user_steps
                ]
            )
        # (c) build the lead (plan-union scope) + assemble its ambient context, then stream +
        # re-home + tail via the shared seam. The RAW user `message` is the human turn; history
        # + plan summary go into the system `context_block`.
        lead = await self._invoker.build_chat_lead(plan.steps, workspace_id)
        lead_ctx = await self._context.assemble_context(
            "lead", message, user_id=user_id, workspace_id=workspace_id
        )
        parts = [p for p in (lead_ctx, history_block) if p]
        if plan.goal or plan.reasoning:
            parts.append(f"[Plan]\nGoal: {plan.goal}\nReasoning: {plan.reasoning}")
        context_block = "\n\n".join(parts)
        async for evt in self._stream_lead_and_complete(
            lead=lead,
            message=message,
            context_block=context_block,
            intent=intent,
            trace=trace,
            user_id=user_id,
            workspace_id=workspace_id,
            effective_mode=effective_mode,
            presence=presence,
            authorization_source=authorization_source,
        ):
            yield evt

    async def _stream_lead_and_complete(
        self,
        *,
        lead,
        message: str,
        context_block: str,
        intent: str | None,
        trace,
        user_id: str,
        workspace_id: str,
        effective_mode: str,
        presence: str,
        authorization_source: str = AuthorizationSource.DIRECT_USER_REQUEST,
    ) -> AsyncGenerator[CoreEvent, None]:
        """Stream a built lead, handle the pause seam, re-home the reply, and run the completion
        tail. Shared verbatim by the planned (:meth:`_run_single_lead`) and planless
        (:meth:`_run_single_lead_planless`) paths — the ONLY difference between them is how the
        lead + ``context_block`` are built, so that setup stays in each caller and this
        streaming/pause/tail seam lives here once (mirrors how ``_emit_completion_tail`` folds the
        tail).

        TERMINAL-TEXT INVARIANT: a turn must never end claiming success with nothing to show
        for it. One lead now carries BOTH the tool work and the reply, so a single failure
        anywhere is a lost reply — under the old per-step arm a dedicated Presenter call still
        ran and spoke. Two ways the reply can go missing, handled below:

        * the stream ends on a sanitized ``error`` frame (``stream_adapter`` catches, yields
          ``error``, and returns WITHOUT ``agent_done``). That turn FAILED — it emits
          ``RunFailed``, not the completion tail, so the client is never told ``done``;
        * ``agent_done`` arrives with empty text. The lead finished but said nothing, which is
          a real outcome — it gets a plain ``Presentation`` saying so, because an empty one is
          not persisted at all."""
        presenter_text = ""
        saw_agent_done = False
        error_frame: dict | None = None
        async for frame in self._invoker.stream_deep_lead(
            lead,
            message=message,
            context_block=context_block,
            user_id=user_id,
            workspace_id=workspace_id,
            intent=intent,
            trace=trace,
            permission_mode=effective_mode,
            # Whether a human is on this turn. `present` keeps today's confirm-and-suspend
            # behaviour; `absent` makes the gates PREPARE a gated write instead of stalling.
            presence=presence,
            # Where this turn came from. Chat's own ``direct_user_request`` keeps trust_gate
            # dormant; a scheduled / WS-fallback turn declares AUTONOMOUS and the gate wakes.
            authorization_source=authorization_source,
        ):
            # PAUSE SEAM (P2.3): the action-time permission gate paused this turn for the
            # user's confirmation. Emit the typed pause event and `return` — ending the
            # generator SKIPS the completion tail (run_completed / surface / learner /
            # RunCompleted) for a suspended turn, while ``_process_core``'s ``finally``
            # still finishes the trace and turn_scope still tears down the MCP sessions.
            # The paused deep checkpoint stays live; the resume path re-enters the thread
            # and runs the tail on the terminal reply. The typed ApprovalRequired REPLACES
            # the raw agent_event_from_sse passthrough so the frame is emitted EXACTLY ONCE.
            #
            # Abandoning the half-consumed stream_deep_lead generator here is INTENTIONAL
            # and safe: stream_deep_agent_events has ALREADY returned after the
            # approval_needed frame (nothing left to drain), and stream_deep_lead set its
            # own paused=True before yielding, so its `if not paused: reap_thread` never
            # runs — the paused checkpoint is preserved for resume.
            if frame.get("event") == "approval_needed":
                yield ApprovalRequired(
                    approval_id=frame.get("approval_id"),
                    capability=frame.get("capability"),
                    risk_level=frame.get("risk_level"),
                    thread_id=frame.get("thread_id"),
                )
                return
            if frame.get("event") == "error":
                # Remember the LAST error frame so the failure below can reuse its already
                # client-safe code/message/correlation_id instead of inventing new ones. The
                # frame is still passed through verbatim (next line) — the client sees it once.
                error_frame = frame
            yield agent_event_from_sse(frame)
            if frame.get("event") == "agent_done":
                saw_agent_done = True
                presenter_text = frame.get("text", "")
                # RE-HOME the presenter output (C-CORR2): stream_deep_lead emits NO
                # Presentation frame, so synthesize it here — else the reply is never
                # persisted (routes_chat persists only on Presentation) and the chat bubble
                # is empty. Keep presenter_text RAW for the shared tail's surface extraction;
                # only the CHAT-VISIBLE text falls back when there is nothing to show.
                reply = strip_surface_blocks(presenter_text).strip()
                yield Presentation(text=reply or _NO_REPLY_TEXT)

        if not saw_agent_done:
            # The stream ended without a terminal reply — an ``error`` frame, or (defensively)
            # any other early end. Either way the turn did NOT succeed, so it must not reach
            # the completion tail: a ``RunCompleted`` here becomes an SSE ``done`` that tells
            # the client the turn finished normally while nothing was ever persisted. Emit the
            # same ``RunFailed`` ``_process_core``'s exception path emits, so the client's
            # existing error handling applies unchanged. No reply is invented and nothing is
            # retried: the lead failed, and the turn says so.
            cid = (error_frame or {}).get("correlation_id") or (
                get_correlation_id() or new_correlation_id()
            )
            code = (error_frame or {}).get("code") or _GENERIC_CODE
            safe_msg = (error_frame or {}).get("message") or _GENERIC_MESSAGE
            logger.error(
                "lead stream ended with no agent_done (workspace=%s user=%s code=%s cid=%s) — "
                "reporting the turn as failed rather than completed",
                workspace_id,
                user_id,
                code,
                cid,
            )
            self._spawn_background(
                self._events.emit_runtime_event(
                    "run_failed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=None,
                    payload={"code": code, "message": safe_msg, "correlation_id": cid},
                )
            )
            yield RunFailed(
                trace_id=trace.trace_id, code=code, message=safe_msg, correlation_id=cid
            )
            return

        # COMPLETION TAIL — single-lead: the learner runs with the RAW user message + reply.
        async for evt in self._emit_completion_tail(
            trace=trace,
            presenter_text=presenter_text,
            user_id=user_id,
            workspace_id=workspace_id,
            message=message,
            intent=intent,
            run_learner=True,
        ):
            yield evt

    async def _run_single_lead_planless(
        self,
        *,
        message: str,
        history_block: str,
        trace,
        user_id: str,
        workspace_id: str,
        effective_mode: str,
        presence: str,
        authorization_source: str = AuthorizationSource.DIRECT_USER_REQUEST,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[CoreEvent, None]:
        """The PLANLESS deep single-lead chat BRANCH (P2.5c) — the Planner never ran.

        Where :meth:`_run_single_lead` receives a ``plan`` (plan-union scope + deterministic
        system.* pre-run + user actions), this path has NO plan: it logs the interaction with
        ``plan=None``, builds ONE lead whose ``capability_scope`` comes from the user's
        authenticated connectors (:meth:`AgentInvoker.build_planless_lead` →
        ``resolve_connector_scope``), and routes the WHOLE turn through it. The lead self-plans
        (write_todos + tool discovery) and calls its own ``system.*`` tools (set_goal / … ) rather
        than a deterministic pre-run. Reuses the shared stream/pause/tail seam
        (:meth:`_stream_lead_and_complete`) verbatim, so the reply / surface / learner / pause
        semantics are identical to the planned path.

        Dropped vs the planned path (by design, D2/D4): classify_intent, the fast-path, the
        Planner, ``PlanReady``, ``resolve_plan_routing``, the deterministic ``system.*`` pre-run,
        and ``UserActionsReady``. ``intent`` is None (never classified). Only reached when
        ``settings.chat_planless`` is on AND the single-lead path is already active — so the
        flag-off path is byte-identical (this method is never entered).

        SECURITY NOTE (write safety unchanged; one deliberate scope-width delta): dropping the
        Planner removes ZERO write GATES — every connector write is still gated by
        ``capability_scope`` (fail-closed) + ``permission_gate`` (ask/auto action-time confirm) +
        ``write_lock``, and ``system.*`` stay ALWAYS-ALLOWED (D5). The Planner was never a gate,
        but it DID incidentally NARROW the per-turn scope to user-implied capabilities; the
        planless lead instead carries a STANDING connector scope (every active+healthy connector's
        caps). In ``ask``/``auto`` (the default) this is fully gated — every connector write
        confirms. In ``bypass`` (permission_gate is a no-op, an explicit entitlement-gated
        opt-out) the wider standing scope means more connector writes are reachable ungated from
        perception-sourced ``history_block`` content — an EXPANSION of the already-tracked
        perception-injection latent enhancement (CLAUDE.md "Two execution paths"), scoped to
        bypass. Bounded by: bypass requires workspace entitlement; perception-triggered runs stay
        on the gated GraphExecutor entirely; and a batch turn that DOES reach this path (the
        scheduler's dispatch actions, the WS action fallback) now declares
        ``authorization_source=AUTONOMOUS``, which wakes ``trust_gate`` for every write on it."""
        ilog_id = await self._plans.log_interaction(
            user_id,
            workspace_id,
            trace.trace_id,
            message_preview=message[:500],
            intent=None,
            plan=None,
            conversation_id=conversation_id,
        )
        yield InteractionLogged(interaction_id=ilog_id)

        # Build the lead from the connector-derived scope (NOT a plan). Its ambient context is
        # the assembled lead context + history — no plan goal/reasoning block (there is no plan).
        lead = await self._invoker.build_planless_lead(user_id, workspace_id)
        lead_ctx = await self._context.assemble_context(
            "lead", message, user_id=user_id, workspace_id=workspace_id
        )
        context_block = "\n\n".join(p for p in (lead_ctx, history_block) if p)
        async for evt in self._stream_lead_and_complete(
            lead=lead,
            message=message,
            context_block=context_block,
            intent=None,
            trace=trace,
            user_id=user_id,
            workspace_id=workspace_id,
            effective_mode=effective_mode,
            presence=presence,
            authorization_source=authorization_source,
        ):
            yield evt

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

        The sibling of ``_process_core`` for the RESUME half of an ask/auto turn: when
        the action-time permission gate PAUSED for the user's confirmation, ``_process_core``
        yielded :class:`ApprovalRequired` and ``return``\\ ed — SKIPPING its completion tail
        (the turn wasn't done). The resume is a SEPARATE HTTP request. This wrapper re-enters
        the paused thread via :meth:`AgentInvoker.resume_deep_lead`, re-streams the
        continuation frames, and — critically — RE-HOMES the reply as a :class:`Presentation`
        and runs the completion tail (surface push + ``RunCompleted``). Without it the
        approved write fires but the reply is never persisted (``routes_chat`` persists only
        on a ``Presentation`` → empty bubble) and no A2UI surface builds — the exact C-CORR2
        failure P1 fixed for the initial turn, un-fixed on resume.

        Yields the SAME ``CoreEvent`` union ``_process_core`` yields, so the resume HTTP
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
            resume_user_message = ""
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
                        # A1: resume_deep_lead piggybacks the ORIGINAL user message here so the
                        # tail can fire the interaction-learner (see the completion tail below).
                        resume_user_message = frame.get("user_message", "")
                        yield Presentation(text=strip_surface_blocks(presenter_text))

                # COMPLETION TAIL (the shared ``_emit_completion_tail``: run_completed →
                # surface push → interaction-learner → RunCompleted). Runs ONLY on the terminal
                # reply (the ``return``s above skip it for a suspended / refused turn).
                #
                # A1: the interaction-learner now fires on an approved resume, at parity with the
                # non-paused ``_run_single_lead`` tail. ``message`` is the ORIGINAL user message —
                # persisted on the Approval at first-pass persist and surfaced back on the
                # ``agent_done`` frame (``resume_user_message``). ``intent`` is not persisted → it
                # stays None (the learner treats it as optional). ``run_learner`` is gated on a
                # truthy message: a pre-A1 approval (no persisted ``user_message``) resolves to ""
                # and must NOT train the learner on an empty ask.
                async for evt in self._emit_completion_tail(
                    trace=trace,
                    presenter_text=presenter_text,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    message=resume_user_message,
                    run_learner=bool(resume_user_message),
                ):
                    yield evt

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
