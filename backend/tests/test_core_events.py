"""Unit tests for the CoreEvent vocabulary (chat-pipeline fold, Phase 2).

Per spec §7: each event serializes to the expected SSE dict (or None for
batch-only events). The fold-to-result-key direction is exercised by the batch
adapter tests in Phase 3.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from src.orchestrator.core_events import (
    AgentStreamEvent,
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
        ):
            dumped = original.model_dump()
            restored = adapter.validate_python(dumped)
            assert restored == original

    def test_events_are_frozen(self):
        evt = TraceStarted(trace_id="t1")
        with pytest.raises(Exception):
            evt.trace_id = "t2"
