"""Basic health check and command endpoint tests."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.api.app import app
from src.models.plans import Plan

client = TestClient(app)


def _make_mock_plan(**overrides):
    plan = MagicMock(spec=Plan)
    defaults = dict(
        plan_id="plan_test123",
        user_id="usr_default",
        trigger_type="command",
        goal="Check today's schedule",
        decision="answer_directly",
        priority="medium",
        risk_level="low",
        execution_mode="auto_execute",
        status="created",
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(plan, k, v)
    return plan


def test_health():
    response = client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@patch("src.api.routes_command.Planner")
def test_command_calls_planner(mock_planner_cls):
    """Command endpoint should call planner and return the plan summary."""
    mock_instance = MagicMock()
    mock_instance.plan_for_command = AsyncMock(return_value=_make_mock_plan())
    mock_planner_cls.return_value = mock_instance

    response = client.post(
        "/v1/jarvis/command",
        json={"command": "What's my schedule today?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["plan_id"] == "plan_test123"
    assert data["decision"] == "answer_directly"
    assert data["summary"] == "Check today's schedule"
    mock_instance.plan_for_command.assert_called_once()


@patch("src.api.routes_command.Planner")
def test_command_handles_planner_error(mock_planner_cls):
    """Command endpoint should return error gracefully if planner fails."""
    mock_instance = MagicMock()
    mock_instance.plan_for_command = AsyncMock(side_effect=RuntimeError("API unavailable"))
    mock_planner_cls.return_value = mock_instance

    response = client.post(
        "/v1/jarvis/command",
        json={"command": "Draft an email to Bob"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "error"
    assert "trouble" in data["summary"].lower()
