"""Tests for plan tracking and run management endpoints."""


class TestPlanEndpoints:
    def test_routes_plans_module_exists(self):
        from src.api import routes_plans

        assert hasattr(routes_plans, "router")

    def test_plan_list_endpoint_registered(self):
        from src.api.routes_plans import router

        paths = [r.path for r in router.routes]
        assert "/v1/plans" in paths

    def test_plan_detail_endpoint_registered(self):
        from src.api.routes_plans import router

        paths = [r.path for r in router.routes]
        assert "/v1/plans/{plan_id}" in paths

    def test_plan_runs_endpoint_registered(self):
        from src.api.routes_plans import router

        paths = [r.path for r in router.routes]
        assert "/v1/plans/{plan_id}/runs" in paths


class TestRunEndpoints:
    def test_run_list_endpoint_exists(self):
        from src.api.routes_runs import router

        paths = [r.path for r in router.routes]
        assert "/v1/runs" in paths

    def test_cancel_endpoint_exists(self):
        from src.api.routes_runs import router

        paths = [r.path for r in router.routes]
        assert "/v1/runs/{run_id}/cancel" in paths

    def test_retry_endpoint_exists(self):
        from src.api.routes_runs import router

        paths = [r.path for r in router.routes]
        assert "/v1/runs/{run_id}/retry" in paths


class TestStatusTransitionAudit:
    def test_transition_run_accepts_emit_event(self):
        import inspect

        from src.services.execution_state import transition_run

        sig = inspect.signature(transition_run)
        assert "emit_event" in sig.parameters

    def test_transition_step_accepts_emit_event(self):
        import inspect

        from src.services.execution_state import transition_step

        sig = inspect.signature(transition_step)
        assert "emit_event" in sig.parameters

    def test_emit_event_called_on_run_transition(self):
        from unittest.mock import MagicMock

        from src.services.execution_state import transition_run

        mock_run = MagicMock()
        mock_run.run_id = "run_test"
        mock_run.status = "pending"
        mock_emit = MagicMock()

        transition_run(mock_run, "running", emit_event=mock_emit)

        mock_emit.assert_called_once_with(
            "run.status_changed",
            {"run_id": "run_test", "from_status": "pending", "to_status": "running"},
        )

    def test_emit_event_called_on_step_transition(self):
        from unittest.mock import MagicMock

        from src.services.execution_state import transition_step

        mock_step = MagicMock()
        mock_step.step_id = "step_test"
        mock_step.status = "pending"
        mock_emit = MagicMock()

        transition_step(mock_step, "ready", emit_event=mock_emit)

        mock_emit.assert_called_once_with(
            "step.status_changed",
            {"step_id": "step_test", "from_status": "pending", "to_status": "ready"},
        )

    def test_emit_event_failure_does_not_crash_transition(self):
        from unittest.mock import MagicMock

        from src.services.execution_state import transition_run

        mock_run = MagicMock()
        mock_run.run_id = "run_test"
        mock_run.status = "pending"
        mock_emit = MagicMock(side_effect=RuntimeError("emit failed"))

        # Should not raise despite emit failure
        transition_run(mock_run, "running", emit_event=mock_emit)
        assert mock_run.status == "running"

    def test_no_emit_event_backward_compatible(self):
        from unittest.mock import MagicMock

        from src.services.execution_state import transition_run

        mock_run = MagicMock()
        mock_run.run_id = "run_test"
        mock_run.status = "pending"

        # Calling without emit_event should work fine
        transition_run(mock_run, "running")
        assert mock_run.status == "running"
