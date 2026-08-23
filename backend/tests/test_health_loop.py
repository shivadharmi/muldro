"""Tests for the unified perception/loop health signal.

derive_loop_health() is a pure function mapping component states to a single
healthy/degraded/unhealthy verdict + human-readable reasons, so the autonomous
loop's health can be checked from one endpoint.
"""


def _ok_inputs():
    return dict(
        observations={"gmail": {"circuit_state": "closed", "consecutive_failures": 0}},
        queues={
            "dlq_pending": 0,
            "dlq_exhausted": 0,
            "approvals_pending": 1,
            "plans_in_flight": 0,
            "plans_stalled": 0,
        },
        runs={"total_runs_today": 5, "failure_rate": 0.0},
        budget={"budget_mode": "normal"},
    )


class TestDeriveLoopHealth:
    def test_all_clear_is_healthy(self):
        from src.api.routes_health import derive_loop_health

        status, reasons = derive_loop_health(**_ok_inputs())
        assert status == "healthy"
        assert reasons == []

    def test_budget_paused_is_unhealthy(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["budget"] = {"budget_mode": "paused"}
        status, reasons = derive_loop_health(**inp)
        assert status == "unhealthy"
        assert any("budget" in r.lower() for r in reasons)

    def test_open_circuit_is_unhealthy(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["observations"] = {"gmail": {"circuit_state": "open", "consecutive_failures": 6}}
        status, reasons = derive_loop_health(**inp)
        assert status == "unhealthy"
        assert any("gmail" in r for r in reasons)

    def test_exhausted_dlq_is_unhealthy(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["queues"] = {"dlq_pending": 0, "dlq_exhausted": 2, "approvals_pending": 0}
        status, reasons = derive_loop_health(**inp)
        assert status == "unhealthy"
        assert any("exhaust" in r.lower() for r in reasons)

    def test_budget_degraded_is_degraded(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["budget"] = {"budget_mode": "degraded"}
        status, reasons = derive_loop_health(**inp)
        assert status == "degraded"
        assert reasons

    def test_transient_failures_are_degraded(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["observations"] = {"slack": {"circuit_state": "closed", "consecutive_failures": 2}}
        status, reasons = derive_loop_health(**inp)
        assert status == "degraded"
        assert any("slack" in r for r in reasons)

    def test_pending_dlq_is_degraded(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["queues"] = {"dlq_pending": 3, "dlq_exhausted": 0, "approvals_pending": 0}
        status, reasons = derive_loop_health(**inp)
        assert status == "degraded"

    def test_high_failure_rate_with_volume_is_degraded(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["runs"] = {"total_runs_today": 8, "failure_rate": 0.75}
        status, reasons = derive_loop_health(**inp)
        assert status == "degraded"

    def test_high_failure_rate_without_volume_ignored(self):
        """A single failed run (low volume) must not flip the loop to degraded."""
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["runs"] = {"total_runs_today": 1, "failure_rate": 1.0}
        status, reasons = derive_loop_health(**inp)
        assert status == "healthy"

    def test_unhealthy_wins_over_degraded(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["budget"] = {"budget_mode": "paused"}  # unhealthy
        inp["queues"] = {"dlq_pending": 3, "dlq_exhausted": 0, "approvals_pending": 0}  # degraded
        status, _ = derive_loop_health(**inp)
        assert status == "unhealthy"


class TestLoopHealthEndpoint:
    def test_endpoint_returns_derived_status(self):
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        from src.api.app import app
        from src.api.deps import get_current_user_id, get_current_workspace_id

        app.dependency_overrides[get_current_user_id] = lambda: "usr_t"
        app.dependency_overrides[get_current_workspace_id] = lambda: "ws_t"
        try:
            with (
                patch(
                    "src.api.routes_health._get_observation_info",
                    new=AsyncMock(
                        return_value={"gmail": {"circuit_state": "open", "consecutive_failures": 6}}
                    ),
                ),
                patch(
                    "src.api.routes_health._get_queue_info",
                    new=AsyncMock(return_value={"dlq_pending": 0, "dlq_exhausted": 0}),
                ),
                patch(
                    "src.api.routes_health._get_run_metrics",
                    new=AsyncMock(return_value={"total_runs_today": 0, "failure_rate": 0.0}),
                ),
                patch(
                    "src.api.routes_health._get_budget_info",
                    new=AsyncMock(return_value={"budget_mode": "normal"}),
                ),
            ):
                resp = TestClient(app).get("/v1/health/loop")

            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "unhealthy"  # open circuit
            assert any("gmail" in r for r in body["reasons"])
            assert "perception" in body
            assert body["checked_at"]
        finally:
            app.dependency_overrides.pop(get_current_user_id, None)
            app.dependency_overrides.pop(get_current_workspace_id, None)


class TestStalledPlans:
    """A plan created but never started is the one impairment with NO other surface.

    It has no run and no approval, so it renders no card and opens onto nothing.
    Before this it was counted as a queued task, which put a number in the
    founder's status bar pointing at something unreachable — the report of the
    bug was exactly "1 task is pending but I don't see any pending task at all".
    """

    def test_a_stalled_plan_is_degraded_and_says_so(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["queues"] = {**inp["queues"], "plans_stalled": 1}
        status, reasons = derive_loop_health(**inp)
        assert status == "degraded"
        assert any("never started" in r for r in reasons)

    def test_the_reason_counts_them(self):
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["queues"] = {**inp["queues"], "plans_stalled": 3}
        _, reasons = derive_loop_health(**inp)
        assert any("3 plans created but never started" in r for r in reasons)

    def test_no_stalled_plans_is_still_healthy(self):
        from src.api.routes_health import derive_loop_health

        status, reasons = derive_loop_health(**_ok_inputs())
        assert status == "healthy"
        assert reasons == []

    def test_a_plan_genuinely_in_flight_is_not_an_impairment(self):
        """`executing` is work happening; only `created` past the stall window counts."""
        from src.api.routes_health import derive_loop_health

        inp = _ok_inputs()
        inp["queues"] = {**inp["queues"], "plans_in_flight": 2}
        status, _ = derive_loop_health(**inp)
        assert status == "healthy"
