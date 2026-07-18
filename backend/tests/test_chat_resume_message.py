"""Tests for ``ChatProcessor.resume_message_events`` (P2.2b).

The processor-layer wrapper that drives the invoker's ``resume_deep_lead`` (P2.2a) and
produces the CoreEvent stream the resume HTTP endpoint (a later task) serves. It is the
missing owner the bare invoker method cannot be: without it the approved write fires but
the reply is never persisted (routes_chat persists only on a ``Presentation``) and no
A2UI surface builds — the exact C-CORR2 failure P1 fixed for the initial turn, un-fixed
on resume [Corr-C1].

These pin the continuation semantics:

* **reply persisted** — an approve continuation whose ``resume_deep_lead`` yields
  text_delta + ``agent_done`` → a ``Presentation(strip_surface_blocks(text))``.
* **surface built** — the shared completion tail extracts + pushes a surface from the RAW
  presenter_text; ``RunCompleted.surface_id`` reflects it.
* **chained pause** — a resumed continuation that re-pauses (2nd write) → a typed
  ``ApprovalRequired`` and STOPS, SKIPPING the completion tail (no ``RunCompleted``, no
  surface) — while ``finish_trace`` still runs (the ``finally``).
* **error passthrough** — ``resume_deep_lead`` refuses (bad decision / guard failure) →
  the client-safe error frame passes through, no tail, ``finish_trace`` still runs.
* **trace finished** — ``finish_trace`` is awaited on every terminal path.

Harness modeled on ``tests/test_chat_single_lead.py`` (``ChatProcessor.__new__`` + a fake
invoker whose ``resume_deep_lead`` is an async-gen yielding scripted SSE frames). NO real
model, DB, or Redis.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
# (P2.2c). The surface seams (``strip_surface_blocks`` / ``extract_surface_spec``) resolve
# in THAT module's namespace, so patches target it.
_MOD = "src.orchestrator.chat_single_lead"


class _Recorder:
    """Captures the ``resume_deep_lead`` call kwargs the processor forwards."""

    def __init__(self) -> None:
        self.resume_calls: list[dict] = []


def _make_resume_chat(*, frames: list[dict], surface_id: str | None = None):
    """Construct a ChatProcessor with every collaborator the resume path touches mocked.

    ``frames`` is the scripted SSE stream the fake ``resume_deep_lead`` yields. ``surface_id``
    is what ``push_presenter_surface`` returns (None = no surface built).
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

    chat._surfaces = MagicMock()
    chat._surfaces.push_presenter_surface = AsyncMock(return_value=surface_id)

    # The interaction-learner is DEFERRED on resume (P2.7): its deps aren't referenced.
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
    stripped reply — the frame routes_chat persists so the chat bubble is not empty."""
    frames = [
        {"event": "agent_start", "agent": "lead", "model": "m"},
        {"event": "text_delta", "agent": "lead", "text": "All "},
        {"event": "text_delta", "agent": "lead", "text": "done."},
        {"event": "agent_done", "agent": "lead", "text": "All done."},
    ]
    chat, rec = _make_resume_chat(frames=frames)
    with patch(f"{_MOD}.strip_surface_blocks", new=lambda t: f"STRIPPED::{t}"):
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
    # Exactly one Presentation, carrying the STRIPPED reply.
    presentations = [e for e in events if isinstance(e, Presentation)]
    assert len(presentations) == 1
    assert presentations[0].text == "STRIPPED::All done."
    # Terminal RunCompleted still closes the turn.
    assert isinstance(events[-1], RunCompleted)
    # The SSE view carries the `response` frame routes_chat persists on.
    assert {"event": "response", "text": "STRIPPED::All done."} in _sse(events)


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


# ── surface built on resume ───────────────────────────────────────────────────────


async def test_resume_surface_built_from_raw_presenter_text():
    """The completion tail extracts a surface from the RAW presenter_text and pushes it;
    RunCompleted.surface_id reflects the pushed surface."""
    frames = [{"event": "agent_done", "agent": "lead", "text": "REPLY_RAW"}]
    chat, _ = _make_resume_chat(frames=frames, surface_id="ui_surf_1")

    spec = MagicMock()
    spec.should_surface = True

    with (
        patch(f"{_MOD}.strip_surface_blocks", new=lambda t: f"STRIPPED::{t}"),
        patch(f"{_MOD}.extract_surface_spec", new=MagicMock(return_value=spec)),
    ):
        events = await _drive(chat)

    # push_presenter_surface was awaited with the RAW presenter_text (not the stripped reply).
    push = chat._surfaces.push_presenter_surface
    push.assert_awaited_once()
    assert push.await_args.kwargs["response_text"] == "REPLY_RAW"
    assert push.await_args.kwargs["run_id"] is None
    # RunCompleted carries the surface id.
    completed = [e for e in events if isinstance(e, RunCompleted)]
    assert len(completed) == 1
    assert completed[0].surface_id == "ui_surf_1"


async def test_resume_no_surface_when_spec_should_not_surface():
    frames = [{"event": "agent_done", "agent": "lead", "text": "REPLY"}]
    chat, _ = _make_resume_chat(frames=frames)

    spec = MagicMock()
    spec.should_surface = False
    with patch(f"{_MOD}.extract_surface_spec", new=MagicMock(return_value=spec)):
        events = await _drive(chat)

    chat._surfaces.push_presenter_surface.assert_not_awaited()
    assert [e for e in events if isinstance(e, RunCompleted)][0].surface_id is None


async def test_resume_surface_push_failure_is_swallowed():
    """A surface push exception is logged, not raised — the turn still completes."""
    frames = [{"event": "agent_done", "agent": "lead", "text": "REPLY"}]
    chat, _ = _make_resume_chat(frames=frames)
    chat._surfaces.push_presenter_surface = AsyncMock(side_effect=RuntimeError("boom"))

    spec = MagicMock()
    spec.should_surface = True
    with patch(f"{_MOD}.extract_surface_spec", new=MagicMock(return_value=spec)):
        events = await _drive(chat)

    completed = [e for e in events if isinstance(e, RunCompleted)]
    assert len(completed) == 1
    assert completed[0].surface_id is None
    chat._trace_manager.finish_trace.assert_awaited_once()


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
    # The completion tail (surface push) never ran on a suspended turn.
    chat._surfaces.push_presenter_surface.assert_not_awaited()
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
    chat._surfaces.push_presenter_surface.assert_not_awaited()
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
