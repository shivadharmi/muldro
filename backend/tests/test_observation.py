"""Tests for perception health tracking (backed by PerceptionState)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import (
    get_current_user,
    get_current_user_id,
    get_current_workspace_id,
    get_session,
)
from src.models.perception_state import PerceptionState
from src.services.heartbeat import HeartbeatService
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


@pytest.fixture(autouse=True)
def _override_auth():
    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[get_current_workspace_id] = lambda: TEST_WORKSPACE_ID
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_current_workspace_id, None)


client = TestClient(app)


# ---------------------------------------------------------------------------
# Route tests: POST /v1/observations/report
# ---------------------------------------------------------------------------


def test_report_observation_creates_new():
    """POST /v1/observations/report creates a new PerceptionState."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=empty_result)

    # refresh is a no-op — the route reads fields from the object it just created
    mock_db.refresh = AsyncMock()

    # Capture the object passed to db.add so we can verify it
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        with patch("src.api.routes_observation._check_stale", return_value=False):
            response = client.post(
                "/v1/observations/report",
                json={"source": "gmail", "event_count": 5},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "gmail"
        assert data["is_stale"] is False
        assert data["circuit_state"] == "closed"
        assert data["event_count"] == 5
        assert len(added_objects) == 1
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_report_observation_updates_existing():
    """POST /v1/observations/report updates existing PerceptionState."""
    existing = MagicMock(spec=PerceptionState)
    existing.source = "gmail"
    existing.last_run_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    existing.last_event_count = 2
    existing.circuit_state = "closed"
    existing.last_error = None
    existing.consecutive_failures = 0
    existing.total_runs = 5

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    found_result = MagicMock()
    found_result.scalar_one_or_none.return_value = existing
    mock_db.execute = AsyncMock(return_value=found_result)

    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        with patch("src.api.routes_observation._check_stale", return_value=False):
            response = client.post(
                "/v1/observations/report",
                json={"source": "gmail", "event_count": 10, "status": "ok"},
            )

        assert response.status_code == 200
        assert existing.last_event_count == 10
        assert existing.circuit_state == "closed"
        mock_db.add.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_report_observation_error_status():
    """POST with status=error reports correctly."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=empty_result)

    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        with patch("src.api.routes_observation._check_stale", return_value=True):
            response = client.post(
                "/v1/observations/report",
                json={
                    "source": "github",
                    "event_count": 0,
                    "status": "error",
                    "error_message": "GitHub API rate limited",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["circuit_state"] == "open"
        assert data["is_stale"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# Route tests: GET /v1/observations/status
# ---------------------------------------------------------------------------


def test_get_observation_status_empty():
    """GET /v1/observations/status returns empty list when no states."""
    mock_db = MagicMock()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=empty_result)

    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        response = client.get("/v1/observations/status")
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_get_observation_status_with_staleness():
    """GET /v1/observations/status computes staleness for each source."""
    fresh = MagicMock(spec=PerceptionState)
    fresh.source = "gmail"
    fresh.last_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    fresh.last_event_count = 3
    fresh.circuit_state = "closed"
    fresh.last_error = None
    fresh.consecutive_failures = 0
    fresh.total_runs = 10

    stale = MagicMock(spec=PerceptionState)
    stale.source = "github"
    stale.last_run_at = datetime.now(timezone.utc) - timedelta(hours=2)
    stale.last_event_count = 1
    stale.circuit_state = "closed"
    stale.last_error = None
    stale.consecutive_failures = 0
    stale.total_runs = 5

    mock_db = MagicMock()
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = [fresh, stale]
    mock_db.execute = AsyncMock(return_value=list_result)

    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        response = client.get("/v1/observations/status")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

        gmail = next(d for d in data if d["source"] == "gmail")
        github = next(d for d in data if d["source"] == "github")
        assert gmail["is_stale"] is False
        assert github["is_stale"] is True  # 2h > 60min threshold
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# Heartbeat: observation health check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_observation_health_flags_stale():
    """Heartbeat should flag stale perception sources."""
    settings = make_mock_settings(
        observation_stale_gmail_minutes=30,
        observation_stale_github_minutes=60,
    )

    stale_ps = MagicMock(spec=PerceptionState)
    stale_ps.source = "gmail"
    stale_ps.last_run_at = datetime.now(timezone.utc) - timedelta(hours=1)
    stale_ps.circuit_state = "closed"

    fresh_ps = MagicMock(spec=PerceptionState)
    fresh_ps.source = "github"
    fresh_ps.last_run_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    fresh_ps.circuit_state = "closed"

    mock_db = MagicMock()
    mock_db.flush = AsyncMock()

    empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))

    obs_result = MagicMock()
    obs_result.scalars.return_value.all.return_value = [stale_ps, fresh_ps]

    mock_db.execute = AsyncMock(
        side_effect=[empty, empty, empty, empty, obs_result, empty, empty, empty]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run(TEST_USER_ID)

    health = result["observation_health"]
    assert len(health) == 2

    gmail_health = next(h for h in health if h["source"] == "gmail")
    github_health = next(h for h in health if h["source"] == "github")
    assert gmail_health["is_stale"] is True
    assert github_health["is_stale"] is False


@pytest.mark.asyncio
async def test_heartbeat_observation_health_error_is_stale():
    """PerceptionState with open circuit should be flagged as stale."""
    settings = make_mock_settings(observation_stale_gmail_minutes=30)

    error_ps = MagicMock(spec=PerceptionState)
    error_ps.source = "gmail"
    error_ps.last_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    error_ps.circuit_state = "open"

    mock_db = MagicMock()
    mock_db.flush = AsyncMock()

    empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    obs_result = MagicMock()
    obs_result.scalars.return_value.all.return_value = [error_ps]

    mock_db.execute = AsyncMock(
        side_effect=[empty, empty, empty, empty, obs_result, empty, empty, empty]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run(TEST_USER_ID)

    health = result["observation_health"]
    assert health[0]["is_stale"] is True
