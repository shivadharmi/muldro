"""Tests for execution durability: cancellation, timeouts, retry backoff, checkpoints."""

import asyncio
import inspect

import pytest

from src.orchestrator.agent_loop import CancellationRequested, _check_cancellation
from src.services.execution_state import STEP_TRANSITIONS


class TestCancellationToken:
    def test_set_event_raises_cancellation(self):
        cancel_event = asyncio.Event()
        cancel_event.set()
        with pytest.raises(CancellationRequested):
            _check_cancellation(cancel_event)

    def test_unset_event_does_not_raise(self):
        cancel_event = asyncio.Event()
        _check_cancellation(cancel_event)  # should not raise

    def test_none_event_does_not_raise(self):
        _check_cancellation(None)  # should not raise

    def test_cancel_events_dict_on_graph_executor(self):
        from src.services.graph_executor import GraphExecutor

        # Verify the class has the cancel_events attribute in __init__
        assert hasattr(GraphExecutor, "__init__")
        source = inspect.getsource(GraphExecutor.__init__)
        assert "_cancel_events" in source

    def test_step_cancelled_transition_valid(self):
        assert "cancelled" in STEP_TRANSITIONS["running"]
        assert "cancelled" in STEP_TRANSITIONS  # terminal state exists
        assert STEP_TRANSITIONS["cancelled"] == set()  # terminal

    def test_agent_loop_accepts_cancel_event(self):
        from src.orchestrator.agent_loop import agent_loop

        sig = inspect.signature(agent_loop)
        assert "cancel_event" in sig.parameters

    def test_cancellation_requested_is_exception(self):
        assert issubclass(CancellationRequested, Exception)
        exc = CancellationRequested("test message")
        assert str(exc) == "test message"


class TestStepTimeout:
    def test_task_step_has_timeout_field(self):
        from src.models.task_graph import TaskStep

        assert hasattr(TaskStep, "timeout_seconds")

    def test_timed_out_transition_valid(self):
        from src.services.execution_state import STEP_TRANSITIONS

        assert "timed_out" in STEP_TRANSITIONS["running"]


class TestRetryBackoff:
    def test_backoff_delay_increases(self):
        from src.services.graph_executor import _compute_retry_delay

        assert _compute_retry_delay(0) == 1
        assert _compute_retry_delay(1) == 2
        assert _compute_retry_delay(2) == 4
        assert _compute_retry_delay(3) == 8

    def test_backoff_capped_at_30(self):
        from src.services.graph_executor import _compute_retry_delay

        assert _compute_retry_delay(5) == 30
        assert _compute_retry_delay(10) == 30


class TestCheckpointValidation:
    def test_resume_run_exists(self):
        from src.services.graph_executor import GraphExecutor

        assert hasattr(GraphExecutor, "resume_run")


class TestVerificationState:
    def test_partially_completed_to_completed_valid(self):
        from src.services.execution_state import RUN_TRANSITIONS

        assert "completed" in RUN_TRANSITIONS["partially_completed"]

    def test_partially_completed_to_failed_valid(self):
        from src.services.execution_state import RUN_TRANSITIONS

        assert "failed" in RUN_TRANSITIONS["partially_completed"]

    def test_verification_promotes_to_completed(self):
        """_run_verification should promote partially_completed to completed on pass."""
        import inspect

        from src.services.graph_executor import GraphExecutor

        source = inspect.getsource(GraphExecutor._run_verification)
        assert "partially_completed" in source
        assert 'transition_run(run, "completed")' in source


class TestStuckRunDetection:
    def test_scheduler_has_health_check_method(self):
        from src.services.scheduler import SchedulerLoop

        assert hasattr(SchedulerLoop, "_tick_run_health_check")


class TestDurableSurfaceUpdates:
    def test_emit_surface_update_method_exists(self):
        """GraphExecutor has _emit_surface_update method."""
        from src.services.graph_executor import GraphExecutor

        assert hasattr(GraphExecutor, "_emit_surface_update")

    def test_ui_surface_model_has_payload(self):
        """UISurface has payload JSONB column for storing surface state."""
        from src.models.ui_state import UISurface

        assert hasattr(UISurface, "payload")

    def test_emit_surface_update_accepts_workspace_id(self):
        """_emit_surface_update signature includes optional workspace_id param."""
        import inspect

        from src.services.graph_executor import GraphExecutor

        sig = inspect.signature(GraphExecutor._emit_surface_update)
        assert "workspace_id" in sig.parameters

    def test_emit_surface_update_persists_to_db(self):
        """_emit_surface_update source contains DB persistence logic."""
        import inspect

        from src.services.graph_executor import GraphExecutor

        source = inspect.getsource(GraphExecutor._emit_surface_update)
        assert "last_surface_update" in source
        assert "persist_db" in source
