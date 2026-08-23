"""Tests for ``ChatProcessor.resume_message_events`` (P2.2b).

The processor-layer wrapper that drives the invoker's ``resume_deep_lead`` (P2.2a) and
produces the CoreEvent stream the resume HTTP endpoint (a later task) serves. It is the
missing owner the bare invoker method cannot be: without it the approved write fires but
the reply is never persisted (routes_chat persists only on a ``Presentation``) — the exact
C-CORR2 failure P1 fixed for the initial turn, un-fixed on resume [Corr-C1].

These pin the continuation semantics:

* **reply persisted** — an approve continuation whose ``resume_deep_lead`` yields
  text_delta + ``agent_done`` → a ``Presentation`` carrying the lead's text.
* **chained pause** — a resumed continuation that re-pauses (2nd write) → a typed
  ``ApprovalRequired`` and STOPS, SKIPPING the completion tail (no ``RunCompleted``) —
  while ``finish_trace`` still runs (the ``finally``).
* **error passthrough** — ``resume_deep_lead`` refuses (bad decision / guard failure) →
  the client-safe error frame passes through, no tail, ``finish_trace`` still runs.
* **trace finished** — ``finish_trace`` is awaited on every terminal path.

Harness modeled on ``tests/test_chat_single_lead.py`` (``ChatProcessor.__new__`` + a fake
invoker whose ``resume_deep_lead`` is an async-gen yielding scripted SSE frames). NO real
model, DB, or Redis.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestrator.core_events import (
    AgentStreamEvent,
    ApprovalRequired,
    Presentation,
    RunCompleted,
    RunFailed,
    TraceStarted,
    core_event_to_sse,
)
from tests.conftest import make_mock_settings

pytestmark = pytest.mark.asyncio

TRACE_ID = "trace_resume"
# ``resume_message_events`` + its shared completion tail live in the single-lead mixin
# (P2.2c), so patches target THAT module's namespace.
_MOD = "src.orchestrator.chat_single_lead"


class _Recorder:
    """Captures the ``resume_deep_lead`` call kwargs the processor forwards."""

    def __init__(self) -> None:
        self.resume_calls: list[dict] = []


def _make_resume_chat(*, frames: list[dict]):
    """Construct a ChatProcessor with every collaborator the resume path touches mocked.

    ``frames`` is the scripted SSE stream the fake ``resume_deep_lead`` yields.
    """
    from src.orchestrator.chat_processor import ChatProcessor

    chat = ChatProcessor.__new__(ChatProcessor)
    rec = _Recorder()

    chat._settings = make_mock_settings()

    trace = MagicMock()
    trace.trace_id = TRACE_ID
    chat._trace_manager = MagicMock()
    chat._trace_manager.start_trace = MagicMock(return_value=trace)
    chat._trace_manager.finish_trace = AsyncMock()

    def _spawn_background(coro):
        # Close the un-awaited runtime-event coroutine so no "never awaited" warning fires.
        if hasattr(coro, "close"):
            coro.close()

    chat._spawn_background = _spawn_background

    chat._events = MagicMock()
    chat._events.emit_runtime_event = AsyncMock()

    # Default: no interaction-learner wired, so the completion tail's ``run_learner=True`` (A1)
    # no-ops (``if run_learner and self._interaction_learner``). The A1 parity test below wires a
    # learner explicitly to prove it fires on an approved resume.
    chat._interaction_learner = None
    chat._ensure_learner_deps = AsyncMock()

    async def _resume_deep_lead(*, approval_id, decision, reason=None, user_id, workspace_id):
        rec.resume_calls.append(
            {
                "approval_id": approval_id,
                "decision": decision,
                "reason": reason,
                "user_id": user_id,
                "workspace_id": workspace_id,
            }
        )
        for f in frames:
            yield f

    chat._invoker = MagicMock()
    chat._invoker.resume_deep_lead = _resume_deep_lead
    return chat, rec


async def _drive(chat, **kw) -> list:
    """Collect the typed ``CoreEvent``s ``resume_message_events`` yields."""
    return [
        evt
        async for evt in chat.resume_message_events(
            approval_id=kw.pop("approval_id", "apr_1"),
            decision=kw.pop("decision", "approve"),
            user_id=kw.pop("user_id", "usr_1"),
            workspace_id=kw.pop("workspace_id", "ws_1"),
            **kw,
        )
    ]


def _sse(events: list) -> list[dict]:
    """The endpoint-facing SSE view (reuses the shared ``core_event_to_sse`` mapping)."""
    return [s for s in (core_event_to_sse(e) for e in events) if s is not None]


# ── reply persisted on resume (C-CORR2 fix, un-fixed then re-fixed) ───────────────


async def test_resume_reply_persisted_as_presentation():
    """An approve continuation (text_delta + agent_done) yields a Presentation carrying the
    reply — the frame routes_chat persists so the chat bubble is not empty."""
    frames = [
        {"event": "agent_start", "agent": "lead", "model": "m"},
        {"event": "text_delta", "agent": "lead", "text": "All "},
        {"event": "text_delta", "agent": "lead", "text": "done."},
        {"event": "agent_done", "agent": "lead", "text": "All done."},
    ]
    chat, rec = _make_resume_chat(frames=frames)
    events = await _drive(chat)

    # resume_deep_lead was driven with the forwarded decision/ids.
    assert rec.resume_calls == [
        {
            "approval_id": "apr_1",
            "decision": "approve",
            "reason": None,
            "user_id": "usr_1",
            "workspace_id": "ws_1",
        }
    ]
    # Exactly one Presentation, carrying the lead's reply.
    presentations = [e for e in events if isinstance(e, Presentation)]
    assert len(presentations) == 1
    assert presentations[0].text == "All done."
    # Terminal RunCompleted still closes the turn.
    assert isinstance(events[-1], RunCompleted)
    # The SSE view carries the `response` frame routes_chat persists on.
    assert {"event": "response", "text": "All done."} in _sse(events)


async def test_resume_reject_reason_is_forwarded():
    """A reject decision + a decline note forward VERBATIM to resume_deep_lead — the invoker
    persists decision_reason so the permission_gate can quote it back on the rejected write."""
    frames = [
        {"event": "agent_start", "agent": "lead", "model": "m"},
        {"event": "agent_done", "agent": "lead", "text": "Not sent — you declined."},
    ]
    chat, rec = _make_resume_chat(frames=frames)
    await _drive(chat, decision="reject", reason="not now")
    assert rec.resume_calls[0]["decision"] == "reject"
    assert rec.resume_calls[0]["reason"] == "not now"


async def test_resume_first_event_is_trace_started():
    frames = [{"event": "agent_done", "agent": "lead", "text": "ok"}]
    chat, _ = _make_resume_chat(frames=frames)
    events = await _drive(chat)
    assert isinstance(events[0], TraceStarted)
    assert events[0].trace_id == TRACE_ID


# ── no surface is built on resume ─────────────────────────────────────────────────


async def test_resume_run_completed_carries_no_surface():
    """The completion tail builds nothing from the reply text: no view is parsed back out
    of what the model wrote, so ``RunCompleted.surface_id`` is always None on a resume."""
    frames = [{"event": "agent_done", "agent": "lead", "text": "REPLY_RAW"}]
    chat, _ = _make_resume_chat(frames=frames)

    events = await _drive(chat)

    completed = [e for e in events if isinstance(e, RunCompleted)]
    assert len(completed) == 1
    assert completed[0].surface_id is None
    chat._trace_manager.finish_trace.assert_awaited_once()


# ── A1: interaction-learner fires on an approved resume ───────────────────────────


async def test_resume_fires_interaction_learner_with_original_user_message():
    """A1: an approved resume fires the interaction-learner at PARITY with the non-paused tail —
    with the ORIGINAL user message. ``resume_deep_lead`` reads it from the Approval's
    artifact_refs and piggybacks it onto the terminal ``agent_done`` frame; the completion tail
    forwards it to ``learn(user_message=...)``. ``intent`` is None (not persisted on resume)."""
    frames = [
        {"event": "text_delta", "agent": "lead", "text": "Booked."},
        {
            "event": "agent_done",
            "agent": "lead",
            "text": "Booked.",
            # resume_deep_lead surfaces the ORIGINAL user message from refs onto this frame.
            "user_message": "book me a flight",
        },
    ]
    chat, _ = _make_resume_chat(frames=frames)
    # Wire a learner (MagicMock.learn returns a MagicMock, which _spawn_background closes — never
    # a real coroutine, so no "never awaited" warning; the call is still recorded).
    learner = MagicMock()
    learner.learn = MagicMock()
    chat._interaction_learner = learner

    events = await _drive(chat)

    # The turn completed (so the tail — and its learner spawn — ran).
    assert isinstance(events[-1], RunCompleted)
    learner.learn.assert_called_once()
    kw = learner.learn.call_args.kwargs
    assert kw["user_message"] == "book me a flight"
    assert kw["agent_response"] == "Booked."  # RAW presenter text (parity with _run_single_lead)
    assert kw["intent"] is None
    assert kw["trace_id"] == TRACE_ID
    assert kw["user_id"] == "usr_1"
    assert kw["workspace_id"] == "ws_1"
    chat._ensure_learner_deps.assert_awaited_once()


async def test_resume_learner_not_fired_when_no_learner_wired():
    """Default harness (no learner) — the tail's run_learner no-ops via the
    ``and self._interaction_learner`` guard, so a resume without a learner still completes."""
    frames = [{"event": "agent_done", "agent": "lead", "text": "done", "user_message": "hi"}]
    chat, _ = _make_resume_chat(frames=frames)  # _interaction_learner = None
    events = await _drive(chat)
    assert isinstance(events[-1], RunCompleted)
    chat._ensure_learner_deps.assert_not_awaited()


async def test_resume_learner_not_fired_on_empty_user_message():
    """A1 guard: a pre-A1 approval (no persisted user_message → "") must NOT train the learner
    on an empty ask, even with a learner wired (``run_learner=bool(resume_user_message)``)."""
    frames = [{"event": "agent_done", "agent": "lead", "text": "done"}]  # no user_message key
    chat, _ = _make_resume_chat(frames=frames)
    learner = MagicMock()
    learner.learn = MagicMock()
    chat._interaction_learner = learner
    events = await _drive(chat)
    assert isinstance(events[-1], RunCompleted)  # turn still completes
    learner.learn.assert_not_called()  # but no empty-message training
    chat._ensure_learner_deps.assert_not_awaited()


# ── chained pause (2nd write in the resumed continuation) ─────────────────────────


async def test_resume_chained_pause_suspends_and_skips_tail():
    """A resumed continuation that re-pauses (approval_needed) emits a typed ApprovalRequired
    and STOPS — no Presentation, no RunCompleted, and the surface tail never runs — while
    finish_trace still runs."""
    frames = [
        {"event": "agent_start", "agent": "lead", "model": "m"},
        {
            "event": "approval_needed",
            "agent": "lead",
            "approval_id": "apr_2",
            "capability": "calendar.create",
            "risk_level": "medium",
            "thread_id": "c:ws_1:t2",
        },
        # A terminal reply the gate only produces AFTER the next resume — must never be reached.
        {"event": "agent_done", "agent": "lead", "text": "SHOULD_NOT_APPEAR"},
    ]
    chat, _ = _make_resume_chat(frames=frames)
    events = await _drive(chat)

    approvals = [e for e in events if isinstance(e, ApprovalRequired)]
    assert len(approvals) == 1
    assert approvals[0].approval_id == "apr_2"
    assert approvals[0].capability == "calendar.create"
    assert approvals[0].risk_level == "medium"
    assert approvals[0].thread_id == "c:ws_1:t2"
    # The turn STOPS: no reply, no completion, no leaked post-approval reply.
    assert not any(isinstance(e, Presentation) for e in events)
    assert not any(isinstance(e, RunCompleted) for e in events)
    assert "SHOULD_NOT_APPEAR" not in "".join(str(e) for e in events)
    # finish_trace STILL ran (the finally survives the early return).
    chat._trace_manager.finish_trace.assert_awaited_once()


async def test_resume_chained_pause_sse_is_approval_needed():
    """The SSE view of the chained pause is the frozen ``approval_needed`` frame the
    frontend consumes to render the confirmation + keep the checkpoint resumable."""
    frames = [
        {
            "event": "approval_needed",
            "agent": "lead",
            "approval_id": "apr_2",
            "capability": "email.send",
            "risk_level": "high",
            "thread_id": "c:ws_1:t2",
        },
    ]
    chat, _ = _make_resume_chat(frames=frames)
    events = await _drive(chat)
    assert {
        "event": "approval_needed",
        "approval_id": "apr_2",
        "capability": "email.send",
        "risk_level": "high",
        "thread_id": "c:ws_1:t2",
    } in _sse(events)


# ── error passthrough (resume_deep_lead refused) ─────────────────────────────────


async def test_resume_error_frame_passes_through_and_skips_tail():
    """resume_deep_lead refuses (bad decision / guard failure): the client-safe error frame
    passes through verbatim, no completion tail runs, and finish_trace still runs."""
    frames = [{"event": "error", "message": "approval not resumable"}]
    chat, _ = _make_resume_chat(frames=frames)
    events = await _drive(chat)

    # The error frame is surfaced (as a pass-through AgentStreamEvent) — verbatim shape kept.
    stream_events = [e for e in events if isinstance(e, AgentStreamEvent)]
    assert len(stream_events) == 1
    assert stream_events[0].payload == {"event": "error", "message": "approval not resumable"}
    # No tail: nothing completed.
    assert not any(isinstance(e, (Presentation, RunCompleted)) for e in events)
    # finish_trace STILL ran.
    chat._trace_manager.finish_trace.assert_awaited_once()
    # SSE view keeps the error frame intact.
    assert {"event": "error", "message": "approval not resumable"} in _sse(events)


async def test_resume_invalid_decision_error_frame_passes_through():
    """The generic ``error`` frame (invalid decision) also passes through and skips the tail."""
    frames = [
        {
            "event": "error",
            "code": "internal_error",
            "message": "Something went wrong. Please try again.",
            "correlation_id": "err_x",
        }
    ]
    chat, _ = _make_resume_chat(frames=frames)
    events = await _drive(chat, decision="maybe")
    stream_events = [e for e in events if isinstance(e, AgentStreamEvent)]
    assert len(stream_events) == 1
    assert stream_events[0].payload["code"] == "internal_error"
    assert not any(isinstance(e, RunCompleted) for e in events)
    chat._trace_manager.finish_trace.assert_awaited_once()


# ── failure path (the wrapper itself raised) ─────────────────────────────────────


async def test_resume_pipeline_exception_yields_run_failed_and_finishes_trace():
    """If a collaborator raises mid-drive, the wrapper emits a terminal RunFailed (mirroring
    _process_core's except) and finish_trace still runs (the finally)."""
    chat, _ = _make_resume_chat(frames=[])

    async def _boom(*, approval_id, decision, reason=None, user_id, workspace_id):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover - makes this an async generator

    chat._invoker.resume_deep_lead = _boom
    events = await _drive(chat)

    failed = [e for e in events if isinstance(e, RunFailed)]
    assert len(failed) == 1
    assert failed[0].trace_id == TRACE_ID
    assert failed[0].correlation_id  # a correlation id is always attached
    chat._trace_manager.finish_trace.assert_awaited_once()


# ── trace finished on EVERY terminal path ────────────────────────────────────────


@pytest.mark.parametrize(
    "frames,decision",
    [
        ([{"event": "agent_done", "agent": "lead", "text": "ok"}], "approve"),  # success tail
        (
            [
                {
                    "event": "approval_needed",
                    "agent": "lead",
                    "approval_id": "apr_2",
                    "capability": "email.send",
                    "risk_level": "high",
                    "thread_id": "c:ws_1:t2",
                }
            ],
            "approve",
        ),  # chained pause
        ([{"event": "error", "message": "approval not found"}], "approve"),  # error frame
    ],
)
async def test_resume_finish_trace_awaited_on_every_path(frames, decision):
    chat, _ = _make_resume_chat(frames=frames)
    await _drive(chat, decision=decision)
    chat._trace_manager.finish_trace.assert_awaited_once()
    assert chat._trace_manager.finish_trace.await_args.kwargs["user_id"] == "usr_1"
    assert chat._trace_manager.finish_trace.await_args.kwargs["workspace_id"] == "ws_1"
