"""Regression guard: verify removed endpoints no longer exist."""


def test_history_router_has_expected_endpoints():
    from src.api.routes_history import router

    paths = [r.path for r in router.routes]
    assert "/v1/history" in paths
    assert "/v1/history/{run_id}" in paths
    assert "/v1/history/{run_id}/retry" in paths
    assert "/v1/runs/{run_id}/cancel" in paths
    assert "/v1/runs/{run_id}/resume" in paths


def test_routes_runs_module_is_deleted():
    """routes_runs.py should no longer be importable."""
    import importlib

    try:
        importlib.import_module("src.api.routes_runs")
        assert False, "src.api.routes_runs should have been deleted"
    except (ImportError, ModuleNotFoundError):
        pass
