"""Tests for execution state machine — transition guards for TaskRun and TaskStep."""

from unittest.mock import MagicMock

import pytest

from src.services.execution_state import (
    RUN_TRANSITIONS,
    STEP_TRANSITIONS,
    InvalidTransitionError,
    transition_run,
    transition_step,
)


def _mock_run(status="pending"):
    run = MagicMock()
    run.run_id = "run_001"
    run.status = status
    return run


def _mock_step(status="pending"):
    step = MagicMock()
    step.step_id = "step_001"
    step.status = status
    return step


# ── Run transitions ──────────────────────────────────────────────


class TestRunTransitions:
    def test_pending_to_running(self):
        run = _mock_run("pending")
        transition_run(run, "running")
        assert run.status == "running"

    def test_pending_to_cancelled(self):
        run = _mock_run("pending")
        transition_run(run, "cancelled")
        assert run.status == "cancelled"

    def test_running_to_completed(self):
        run = _mock_run("running")
        transition_run(run, "completed")
        assert run.status == "completed"

    def test_running_to_failed(self):
        run = _mock_run("running")
        transition_run(run, "failed")
        assert run.status == "failed"

    def test_running_to_paused(self):
        run = _mock_run("running")
        transition_run(run, "paused")
        assert run.status == "paused"

    def test_running_to_awaiting_approval(self):
        run = _mock_run("running")
        transition_run(run, "awaiting_approval")
        assert run.status == "awaiting_approval"

    def test_paused_to_running(self):
        run = _mock_run("paused")
        transition_run(run, "running")
        assert run.status == "running"

    def test_awaiting_approval_to_running(self):
        run = _mock_run("awaiting_approval")
        transition_run(run, "running")
        assert run.status == "running"

    def test_failed_to_pending_retry(self):
        run = _mock_run("failed")
        transition_run(run, "pending")
        assert run.status == "pending"

    def test_completed_cannot_transition_to_running(self):
        run = _mock_run("completed")
        with pytest.raises(InvalidTransitionError) as exc_info:
            transition_run(run, "running")
        assert "completed → running" in str(exc_info.value)
        assert run.status == "completed"  # Unchanged

    def test_cancelled_cannot_transition(self):
        run = _mock_run("cancelled")
        with pytest.raises(InvalidTransitionError):
            transition_run(run, "running")

    def test_invalid_pending_to_completed(self):
        run = _mock_run("pending")
        with pytest.raises(InvalidTransitionError):
            transition_run(run, "completed")

    def test_invalid_pending_to_failed(self):
        run = _mock_run("pending")
        with pytest.raises(InvalidTransitionError):
            transition_run(run, "failed")

    def test_pending_to_blocked(self):
        run = _mock_run("pending")
        transition_run(run, "blocked")
        assert run.status == "blocked"

    def test_blocked_to_pending(self):
        run = _mock_run("blocked")
        transition_run(run, "pending")
        assert run.status == "pending"

    def test_running_to_partially_completed(self):
        run = _mock_run("running")
        transition_run(run, "partially_completed")
        assert run.status == "partially_completed"

    def test_partially_completed_to_running(self):
        run = _mock_run("partially_completed")
        transition_run(run, "running")
        assert run.status == "running"

    def test_completed_to_archived(self):
        run = _mock_run("completed")
        transition_run(run, "archived")
        assert run.status == "archived"

    def test_timed_out_to_pending(self):
        run = _mock_run("timed_out")
        transition_run(run, "pending")
        assert run.status == "pending"

    def test_all_run_statuses_have_transitions(self):
        """Every status in the state machine should be defined."""
        expected = {
            "pending",
            "running",
            "paused",
            "awaiting_approval",
            "awaiting_input",
            "blocked",
            "partially_completed",
            "completed",
            "failed",
            "cancelled",
            "archived",
            "timed_out",
        }
        assert set(RUN_TRANSITIONS.keys()) == expected


# ── Step transitions ─────────────────────────────────────────────


class TestStepTransitions:
    def test_pending_to_ready(self):
        step = _mock_step("pending")
        transition_step(step, "ready")
        assert step.status == "ready"

    def test_ready_to_running(self):
        step = _mock_step("ready")
        transition_step(step, "running")
        assert step.status == "running"

    def test_running_to_completed(self):
        step = _mock_step("running")
        transition_step(step, "completed")
        assert step.status == "completed"

    def test_running_to_failed(self):
        step = _mock_step("running")
        transition_step(step, "failed")
        assert step.status == "failed"

    def test_running_to_waiting_approval(self):
        step = _mock_step("running")
        transition_step(step, "waiting_approval")
        assert step.status == "waiting_approval"

    def test_waiting_approval_to_running(self):
        step = _mock_step("waiting_approval")
        transition_step(step, "running")
        assert step.status == "running"

    def test_failed_to_pending_retry(self):
        step = _mock_step("failed")
        transition_step(step, "pending")
        assert step.status == "pending"

    def test_pending_to_skipped(self):
        step = _mock_step("pending")
        transition_step(step, "skipped")
        assert step.status == "skipped"

    def test_ready_to_skipped(self):
        step = _mock_step("ready")
        transition_step(step, "skipped")
        assert step.status == "skipped"

    def test_completed_cannot_transition(self):
        step = _mock_step("completed")
        with pytest.raises(InvalidTransitionError):
            transition_step(step, "running")

    def test_skipped_cannot_transition(self):
        step = _mock_step("skipped")
        with pytest.raises(InvalidTransitionError):
            transition_step(step, "running")

    def test_pending_to_blocked(self):
        step = _mock_step("pending")
        transition_step(step, "blocked")
        assert step.status == "blocked"

    def test_blocked_to_pending(self):
        step = _mock_step("blocked")
        transition_step(step, "pending")
        assert step.status == "pending"

    def test_running_to_timed_out(self):
        step = _mock_step("running")
        transition_step(step, "timed_out")
        assert step.status == "timed_out"

    def test_timed_out_to_pending(self):
        step = _mock_step("timed_out")
        transition_step(step, "pending")
        assert step.status == "pending"

    def test_all_step_statuses_have_transitions(self):
        expected = {
            "pending",
            "ready",
            "running",
            "waiting_approval",
            "awaiting_input",
            "blocked",
            "completed",
            "failed",
            "skipped",
            "timed_out",
        }
        assert set(STEP_TRANSITIONS.keys()) == expected


# ── Retry lifecycle ──────────────────────────────────────────────


class TestRetryLifecycle:
    def test_step_retry_cycle(self):
        """A step should be able to go through the full retry cycle."""
        step = _mock_step("pending")

        # First attempt
        transition_step(step, "ready")
        transition_step(step, "running")
        transition_step(step, "failed")

        # Retry
        transition_step(step, "pending")
        transition_step(step, "ready")
        transition_step(step, "running")
        transition_step(step, "completed")

        assert step.status == "completed"

    def test_run_retry_cycle(self):
        """A run should be able to go through the retry cycle."""
        run = _mock_run("pending")

        transition_run(run, "running")
        transition_run(run, "failed")
        transition_run(run, "pending")
        transition_run(run, "running")
        transition_run(run, "completed")

        assert run.status == "completed"

    def test_approval_flow(self):
        """Run through approval → resume → complete."""
        run = _mock_run("pending")
        step = _mock_step("pending")

        transition_run(run, "running")
        transition_step(step, "ready")
        transition_step(step, "running")
        transition_step(step, "waiting_approval")
        transition_run(run, "awaiting_approval")

        # Approved → resume
        transition_step(step, "running")
        transition_run(run, "running")
        transition_step(step, "completed")
        transition_run(run, "completed")

        assert run.status == "completed"
        assert step.status == "completed"


# ── Error details ────────────────────────────────────────────────


class TestInvalidTransitionError:
    def test_error_message_includes_context(self):
        run = _mock_run("completed")
        with pytest.raises(InvalidTransitionError) as exc_info:
            transition_run(run, "running")
        err = exc_info.value
        assert err.entity_type == "run"
        assert err.entity_id == "run_001"
        assert err.from_status == "completed"
        assert err.to_status == "running"

    def test_step_error_includes_context(self):
        step = _mock_step("skipped")
        with pytest.raises(InvalidTransitionError) as exc_info:
            transition_step(step, "running")
        err = exc_info.value
        assert err.entity_type == "step"
        assert err.entity_id == "step_001"
