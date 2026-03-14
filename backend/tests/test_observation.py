"""Tests for observation health tracking."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_current_user, get_session
from src.models.observation import ObservationStatus
from src.services.heartbeat import HeartbeatService
from tests.conftest import make_mock_settings


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_current_user] = lambda: "usr_default"
    yield
    app.dependency_overrides.pop(get_current_user, None)


client = TestClient(app)


# ---------------------------------------------------------------------------
# Route tests: POST /v1/observations/report
# ---------------------------------------------------------------------------


def test_report_observation_creates_new():
    """POST /v1/observations/report creates a new observation status."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    # No existing observation found
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=empty_result)

    # After commit + refresh, the ORM object has the values
    obs_obj = MagicMock(spec=ObservationStatus)
    obs_obj.source = "gmail"
    obs_obj.last_observed_at = datetime.now(timezone.utc)
    obs_obj.items_found = 5
    obs_obj.items_ingested = 3
    obs_obj.status = "ok"
    obs_obj.error_message = None

    mock_db.refresh = AsyncMock()

    # Patch _check_stale to return False
    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        with patch("src.api.routes_observation._check_stale", return_value=False):
            response = client.post(
                "/v1/observations/report",
                json={"source": "gmail", "items_found": 5, "items_ingested": 3},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "gmail"
        assert data["is_stale"] is False
        mock_db.add.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_report_observation_updates_existing():
    """POST /v1/observations/report updates existing observation status."""
    existing_obs = MagicMock(spec=ObservationStatus)
    existing_obs.source = "gmail"
    existing_obs.last_observed_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    existing_obs.items_found = 2
    existing_obs.items_ingested = 1
    existing_obs.status = "ok"
    existing_obs.error_message = None

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    found_result = MagicMock()
    found_result.scalar_one_or_none.return_value = existing_obs
    mock_db.execute = AsyncMock(return_value=found_result)

    app.dependency_overrides[get_session] = lambda: mock_db

    try:
        with patch("src.api.routes_observation._check_stale", return_value=False):
            response = client.post(
                "/v1/observations/report",
                json={
                    "source": "gmail",
                    "items_found": 10,
                    "items_ingested": 7,
                    "status": "ok",
                },
            )

        assert response.status_code == 200
        # Verify the existing object was updated
        assert existing_obs.items_found == 10
        assert existing_obs.items_ingested == 7
        # Should NOT add a new object
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
                    "items_found": 0,
                    "items_ingested": 0,
                    "status": "error",
                    "error_message": "GitHub API rate limited",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["is_stale"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# Route tests: GET /v1/observations/status
# ---------------------------------------------------------------------------


def test_get_observation_status_empty():
    """GET /v1/observations/status returns empty list when no observations."""
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
    fresh_obs = MagicMock(spec=ObservationStatus)
    fresh_obs.source = "gmail"
    fresh_obs.last_observed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    fresh_obs.items_found = 3
    fresh_obs.items_ingested = 2
    fresh_obs.status = "ok"
    fresh_obs.error_message = None

    stale_obs = MagicMock(spec=ObservationStatus)
    stale_obs.source = "github"
    stale_obs.last_observed_at = datetime.now(timezone.utc) - timedelta(hours=2)
    stale_obs.items_found = 1
    stale_obs.items_ingested = 0
    stale_obs.status = "ok"
    stale_obs.error_message = None

    mock_db = MagicMock()
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = [fresh_obs, stale_obs]
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
    """Heartbeat should flag stale observation sources."""
    settings = make_mock_settings(
        observation_stale_gmail_minutes=30,
        observation_stale_github_minutes=60,
    )

    stale_obs = MagicMock(spec=ObservationStatus)
    stale_obs.source = "gmail"
    stale_obs.last_observed_at = datetime.now(timezone.utc) - timedelta(hours=1)
    stale_obs.status = "ok"

    fresh_obs = MagicMock(spec=ObservationStatus)
    fresh_obs.source = "github"
    fresh_obs.last_observed_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    fresh_obs.status = "ok"

    mock_db = MagicMock()
    mock_db.flush = AsyncMock()

    # Build results: memories, plans, approvals, invalidate_plans, observation_status
    empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))

    obs_result = MagicMock()
    obs_result.scalars.return_value.all.return_value = [stale_obs, fresh_obs]

    # 5 original calls + 3 from _reflect_on_schedules
    mock_db.execute = AsyncMock(
        side_effect=[empty, empty, empty, empty, obs_result, empty, empty, empty]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run("usr_default")

    health = result["observation_health"]
    assert len(health) == 2

    gmail_health = next(h for h in health if h["source"] == "gmail")
    github_health = next(h for h in health if h["source"] == "github")
    assert gmail_health["is_stale"] is True
    assert github_health["is_stale"] is False
    assert stale_obs.status == "stale"


@pytest.mark.asyncio
async def test_heartbeat_observation_health_error_is_stale():
    """Observation source with error status should be flagged as stale."""
    settings = make_mock_settings(observation_stale_gmail_minutes=30)

    error_obs = MagicMock(spec=ObservationStatus)
    error_obs.source = "gmail"
    error_obs.last_observed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    error_obs.status = "error"

    mock_db = MagicMock()
    mock_db.flush = AsyncMock()

    empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    obs_result = MagicMock()
    obs_result.scalars.return_value.all.return_value = [error_obs]

    # 5 original calls + 3 from _reflect_on_schedules
    mock_db.execute = AsyncMock(
        side_effect=[empty, empty, empty, empty, obs_result, empty, empty, empty]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run("usr_default")

    health = result["observation_health"]
    assert health[0]["is_stale"] is True
    assert error_obs.status == "stale"
