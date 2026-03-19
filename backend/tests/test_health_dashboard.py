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
        assert result.status == "ok"
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
