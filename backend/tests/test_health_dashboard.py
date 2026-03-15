"""Tests for system health dashboard endpoint."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.api.app import app

client = TestClient(app)


class TestHealthDashboard:
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
    def test_dashboard_returns_all_sections(self, _bud, _q, _obs, _ag):
        resp = client.get("/v1/system/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["budget"]["daily_spend_usd"] == 1.23
        assert data["budget"]["budget_mode"] == "normal"
        assert data["queues"]["dlq_pending"] == 0
        assert data["observations"] == {}
        assert data["agents"] == {}

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
    def test_dashboard_reflects_high_usage(self, _bud, _q, _obs, _ag):
        resp = client.get("/v1/system/dashboard")
        data = resp.json()
        assert data["budget"]["budget_mode"] == "paused"
        assert data["queues"]["plans_in_flight"] == 3
