"""Tests for system health dashboard endpoint."""

from unittest.mock import AsyncMock, patch

from src.api.routes_health import HealthDashboardResponse, system_dashboard
from tests.conftest import TEST_USER_ID


class TestHealthDashboard:
    @patch("src.api.routes_health._get_run_metrics", new_callable=AsyncMock, return_value={})
    @patch("src.api.routes_health._get_trace_metrics", new_callable=AsyncMock, return_value={})
    @patch("src.api.routes_health._get_agent_info", new_callable=AsyncMock, return_value={})
    @patch(
        "src.api.routes_health._get_observation_info",
        new_callable=AsyncMock,
        return_value={},
    )
    @patch(
        "src.api.routes_health._get_queue_info",
        new_callable=AsyncMock,
        return_value={"dlq_pending": 0, "approvals_pending": 0, "plans_in_flight": 0},
    )
    @patch(
        "src.api.routes_health._get_budget_info",
        new_callable=AsyncMock,
        return_value={
            "daily_spend_usd": 1.23,
            "daily_limit_usd": 5.0,
            "percent_used": 24.6,
            "budget_mode": "normal",
        },
    )
    async def test_dashboard_returns_all_sections(self, _bud, _q, _obs, _ag, _tr, _run):
        result = await system_dashboard(user_id=TEST_USER_ID)
        assert isinstance(result, HealthDashboardResponse)
        # "ok" was a fourth value nothing consumed: the status bar switches on
        # healthy/degraded/unhealthy and read every workspace as "Unknown".
        assert result.status == "healthy"
        assert result.budget["daily_spend_usd"] == 1.23
        assert result.budget["budget_mode"] == "normal"
        assert result.queues["dlq_pending"] == 0
        assert result.observations == {}
        assert result.agents == {}

    @patch("src.api.routes_health._get_run_metrics", new_callable=AsyncMock, return_value={})
    @patch("src.api.routes_health._get_trace_metrics", new_callable=AsyncMock, return_value={})
    @patch("src.api.routes_health._get_agent_info", new_callable=AsyncMock, return_value={})
    @patch(
        "src.api.routes_health._get_observation_info",
        new_callable=AsyncMock,
        return_value={},
    )
    @patch(
        "src.api.routes_health._get_queue_info",
        new_callable=AsyncMock,
        return_value={"dlq_pending": 2, "approvals_pending": 1, "plans_in_flight": 3},
    )
    @patch(
        "src.api.routes_health._get_budget_info",
        new_callable=AsyncMock,
        return_value={
            "daily_spend_usd": 4.8,
            "daily_limit_usd": 5.0,
            "percent_used": 96.0,
            "budget_mode": "paused",
        },
    )
    async def test_dashboard_reflects_high_usage(self, _bud, _q, _obs, _ag, _tr, _run):
        result = await system_dashboard(user_id=TEST_USER_ID)
        assert result.budget["budget_mode"] == "paused"
        assert result.queues["plans_in_flight"] == 3


class TestAgentInfoTokenAggregation:
    """ORCH-P2-1: per-agent token totals must NOT double-count per-tool
    attribution rows (trigger='tool:*'), which are a breakdown of the
    authoritative loop-level row.
    """

    async def test_get_agent_info_excludes_per_tool_breakdown_rows(self):
        from unittest.mock import MagicMock

        from src.api.routes_health import _get_agent_info

        captured = {}

        class FakeDB:
            async def execute(self, stmt):
                captured["stmt"] = stmt
                result = MagicMock()
                result.all.return_value = []
                return result

        class FakeCM:
            async def __aenter__(self):
                return FakeDB()

            async def __aexit__(self, *args):
                return False

        class FakeFactory:
            def __call__(self):
                return FakeCM()

        with patch(
            "src.models.database.get_session_factory",
            return_value=FakeFactory(),
        ):
            await _get_agent_info("ws_test")

        assert "stmt" in captured, "query was never executed"
        sql = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
        # The per-tool breakdown rows must be filtered out of the aggregate so a
        # SUM over input/output tokens (and the call count) is not doubled.
        # Assert the full rendered predicate (column + negation + pattern) so the
        # test cannot pass on a wrong-but-similar filter (wrong column, or a
        # non-negated LIKE).
        assert "token_usage.trigger not like 'tool:%'" in sql.lower(), (
            f"aggregate does not exclude tool:* breakdown rows on the trigger column: {sql}"
        )


class TestDashboardReportsRealHealth:
    """The dashboard is the endpoint the status bar reads, so it must report the
    COMPUTED verdict, not a constant.

    `status` was hardcoded `"ok"` — a fourth value nothing consumed. The status
    bar switches on healthy/degraded/unhealthy, so every workspace read
    "Unknown" for ever, while `derive_loop_health` sat in the same module
    computing the real answer for `/v1/health/loop`, which nothing called.

    Asserted with a NON-default verdict on purpose: the field defaults to
    "healthy", so a healthy fixture passes whether or not the wiring exists.
    """

    @patch("src.api.routes_health._get_run_metrics", new_callable=AsyncMock, return_value={})
    @patch("src.api.routes_health._get_trace_metrics", new_callable=AsyncMock, return_value={})
    @patch("src.api.routes_health._get_agent_info", new_callable=AsyncMock, return_value={})
    @patch(
        "src.api.routes_health._get_observation_info",
        new_callable=AsyncMock,
        return_value={},
    )
    @patch(
        "src.api.routes_health._get_queue_info",
        new_callable=AsyncMock,
        return_value={
            "dlq_pending": 0,
            "dlq_exhausted": 0,
            "approvals_pending": 0,
            "plans_in_flight": 0,
            "plans_stalled": 1,
        },
    )
    @patch(
        "src.api.routes_health._get_budget_info",
        new_callable=AsyncMock,
        return_value={"budget_mode": "normal"},
    )
    async def test_a_stalled_plan_makes_the_dashboard_say_degraded(
        self, _bud, _q, _obs, _ag, _tr, _run
    ):
        result = await system_dashboard(user_id=TEST_USER_ID)
        assert result.status == "degraded"

    @patch("src.api.routes_health._get_run_metrics", new_callable=AsyncMock, return_value={})
    @patch("src.api.routes_health._get_trace_metrics", new_callable=AsyncMock, return_value={})
    @patch("src.api.routes_health._get_agent_info", new_callable=AsyncMock, return_value={})
    @patch(
        "src.api.routes_health._get_observation_info",
        new_callable=AsyncMock,
        return_value={},
    )
    @patch(
        "src.api.routes_health._get_queue_info",
        new_callable=AsyncMock,
        return_value={"dlq_pending": 0, "dlq_exhausted": 0, "approvals_pending": 0},
    )
    @patch(
        "src.api.routes_health._get_budget_info",
        new_callable=AsyncMock,
        return_value={"budget_mode": "paused"},
    )
    async def test_a_paused_budget_makes_the_dashboard_say_unhealthy(
        self, _bud, _q, _obs, _ag, _tr, _run
    ):
        result = await system_dashboard(user_id=TEST_USER_ID)
        assert result.status == "unhealthy"
