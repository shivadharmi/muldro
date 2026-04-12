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
