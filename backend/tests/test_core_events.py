"""Unit tests for the CoreEvent vocabulary (chat-pipeline fold, Phase 2).

Per spec §7: each event serializes to the expected SSE dict (or None for
batch-only events). The fold-to-result-key direction is exercised by the batch
adapter tests in Phase 3.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from src.orchestrator.core_events import (
    AgentDone,
    AgentStarted,
    AgentStreamEvent,
    AgentTextDelta,
    AgentThinking,
    AgentToolCall,
    AgentToolResult,
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


class TestSseMapping:
    def test_trace_started(self):
        assert core_event_to_sse(TraceStarted(trace_id="t1")) == {
            "event": "trace",
            "trace_id": "t1",
        }

    def test_intent_classified(self):
        assert core_event_to_sse(IntentClassified(intent="greeting", confidence=0.9)) == {
            "event": "intent",
            "intent": "greeting",
            "confidence": 0.9,
        }

    def test_plan_ready_drops_summary(self):
        # summary is batch-only; the SSE plan event must not carry it.
        sse = core_event_to_sse(PlanReady(plan={"goal": "g"}, run_id=None, summary="batch only"))
        assert sse == {"event": "plan", "plan": {"goal": "g"}, "run_id": None}

    def test_agent_stream_passthrough(self):
        payload = {"event": "agent_done", "agent": "planner", "text": "hi", "cost_usd": 0.01}
        assert core_event_to_sse(AgentStreamEvent(payload=payload)) == payload

    def test_step_error(self):
        assert core_event_to_sse(StepError(step_id="s1", error="no tools")) == {
            "event": "step_error",
            "step_id": "s1",
            "error": "no tools",
        }

    def test_plan_mode_step_skipped(self):
        assert core_event_to_sse(
            PlanModeStepSkipped(plan_id="plan_1", message="Review and approve.")
        ) == {"event": "plan_ready", "plan_id": "plan_1", "message": "Review and approve."}

    def test_user_actions_ready(self):
        steps = [{"description": "confirm", "context": "ok"}]
        assert core_event_to_sse(UserActionsReady(steps=steps)) == {
            "event": "user_actions",
            "steps": steps,
        }

    def test_presentation(self):
        assert core_event_to_sse(Presentation(text="hello")) == {
            "event": "response",
            "text": "hello",
        }

    def test_approval_required_maps_to_frozen_approval_needed_frame(self):
        # The frontend consumes this frozen frame to render the confirmation prompt AND to
        # keep the paused checkpoint resumable — a dropped/renamed key strands the pause.
        assert core_event_to_sse(
            ApprovalRequired(
                approval_id="apr_1",
                capability="email.send",
                risk_level="high",
                thread_id="c:ws_1:t1",
            )
        ) == {
            "event": "approval_needed",
            "approval_id": "apr_1",
            "capability": "email.send",
            "risk_level": "high",
            "thread_id": "c:ws_1:t1",
        }

    def test_run_completed_without_surface(self):
        assert core_event_to_sse(RunCompleted(trace_id="t1", run_id=None)) == {
            "event": "done",
            "trace_id": "t1",
            "run_id": None,
        }

    def test_run_completed_with_surface(self):
        assert core_event_to_sse(RunCompleted(trace_id="t1", run_id=None, surface_id="surf_1")) == {
            "event": "done",
            "trace_id": "t1",
            "run_id": None,
            "surface_id": "surf_1",
        }

    def test_run_failed_matches_safe_error_event_shape(self):
        sse = core_event_to_sse(
            RunFailed(trace_id="t1", code="internal_error", message="oops", correlation_id="cid_1")
        )
        assert sse == {
            "event": "error",
            "code": "internal_error",
            "message": "oops",
            "correlation_id": "cid_1",
        }

    @pytest.mark.parametrize(
        "event",
        [
            InteractionLogged(interaction_id="ilog_1"),
            StepResult(key="step_0_email.read", output="done"),
            SystemStepResult(key="system_system.respond", output="ok"),
        ],
    )
    def test_batch_only_events_drop_from_stream(self, event):
        assert core_event_to_sse(event) is None


class TestDiscriminatedUnion:
    def test_round_trips_on_type_discriminator(self):
        adapter = TypeAdapter(CoreEvent)
        for original in (
            TraceStarted(trace_id="t1"),
            PlanReady(plan={"goal": "g"}),
            RunFailed(trace_id="t1", code="c", message="m", correlation_id="cid"),
            ApprovalRequired(
                approval_id="apr_1", capability="email.send", risk_level="high", thread_id="th_1"
            ),
        ):
            dumped = original.model_dump()
            restored = adapter.validate_python(dumped)
            assert restored == original

    def test_events_are_frozen(self):
        evt = TraceStarted(trace_id="t1")
        with pytest.raises(Exception):
            evt.trace_id = "t2"


# Full agent-loop SSE dicts as `_call_agent_stream` produces them in production.
_AGENT_SSE_DICTS = [
    {"event": "agent_start", "agent": "planner", "model": "claude-opus"},
    {"event": "thinking", "agent": "planner", "text": "hmm", "is_thinking": True},
    {"event": "text_delta", "agent": "planner", "text": "hello"},
    {"event": "tool_call", "agent": "executor", "tool": "send", "input": {"to": "x"}},
    {
        "event": "tool_result",
        "agent": "executor",
        "tool": "send",
        "result": {"ok": True},
        "blocked": False,
        "latency_ms": 42,
    },
    {
        "event": "agent_done",
        "agent": "planner",
        "text": "done",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "tools_called": ["send"],
        "latency_ms": 100,
        "cost_usd": 0.001,
    },
]


class TestAgentEventRoundTrip:
    """Critical fidelity guarantee: a full production agent SSE dict survives
    dict -> typed CoreEvent (agent_event_from_sse) -> dict (core_event_to_sse)
    BYTE-IDENTICALLY, so re-typing the agent-loop stream changes no SSE output."""

    @pytest.mark.parametrize("sse", _AGENT_SSE_DICTS, ids=lambda d: d["event"])
    def test_full_dict_round_trips_byte_identical(self, sse):
        typed = agent_event_from_sse(sse)
        assert core_event_to_sse(typed) == sse

    @pytest.mark.parametrize(
        "sse,expected_type",
        [
            (_AGENT_SSE_DICTS[0], AgentStarted),
            (_AGENT_SSE_DICTS[1], AgentThinking),
            (_AGENT_SSE_DICTS[2], AgentTextDelta),
            (_AGENT_SSE_DICTS[3], AgentToolCall),
            (_AGENT_SSE_DICTS[4], AgentToolResult),
            (_AGENT_SSE_DICTS[5], AgentDone),
        ],
    )
    def test_maps_to_expected_typed_event(self, sse, expected_type):
        assert isinstance(agent_event_from_sse(sse), expected_type)

    def test_agent_done_tools_called_is_list_from_production(self):
        """Production ``agent_done`` carries ``tools_called`` as a list of tool
        names (``LoopDone.tools_called: list[str]``), not an int. Regression
        for the AgentDone validation crash that killed the live chat stream."""
        sse = {
            "event": "agent_done",
            "agent": "executor",
            "text": "done",
            "input_tokens": 10,
            "output_tokens": 5,
            "tools_called": ["search_memory", "send_email"],
            "latency_ms": 100,
            "cost_usd": 0.002,
        }
        typed = agent_event_from_sse(sse)
        assert isinstance(typed, AgentDone)
        assert typed.tools_called == ["search_memory", "send_email"]
        assert core_event_to_sse(typed)["tools_called"] == ["search_memory", "send_email"]

    def test_unknown_event_falls_back_to_passthrough(self):
        # The sanitized LoopError / Unknown-agent frames aren't typed token
        # events; they pass through verbatim as AgentStreamEvent.
        err = {"event": "error", "agent": "presenter", "code": "internal_error", "message": "x"}
        typed = agent_event_from_sse(err)
        assert isinstance(typed, AgentStreamEvent)
        assert core_event_to_sse(typed) == err

        unknown = {"event": "error", "message": "Unknown agent: ghost"}
        assert core_event_to_sse(agent_event_from_sse(unknown)) == unknown


class TestValidationFailed:
    def test_maps_to_bare_error_frame(self):
        assert core_event_to_sse(ValidationFailed(message="Empty message")) == {
            "event": "error",
            "message": "Empty message",
        }
