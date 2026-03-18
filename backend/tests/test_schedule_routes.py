"""Tests for schedule CRUD routes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_current_user_id, get_session
from src.models.schedules import Schedule
from tests.conftest import TEST_USER_ID


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def client(mock_db):
    app = create_app()

    # Mock user object
    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[get_session] = lambda: mock_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _make_db_schedule(**overrides) -> MagicMock:
    """Factory for mock Schedule model instances."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        schedule_id="sched_test_001",
        user_id=TEST_USER_ID,
        name="test-observe-gmail",
        description="Check Gmail every 15 minutes",
        schedule_type="recurring",
        cron_expr="*/15 * * * *",
        run_at=None,
        action_type="observe_source",
        action_config={"source": "gmail"},
        enabled=True,
        last_run_at=None,
        next_run_at=now + timedelta(minutes=10),
        run_count=0,
        consecutive_failures=0,
        last_error=None,
        source="user",
        priority="medium",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    sched = MagicMock(spec=Schedule)
    for k, v in defaults.items():
        setattr(sched, k, v)
    return sched


class TestCreateSchedule:
    def test_create_recurring_schedule(self, client, mock_db):
        """POST /v1/schedules with recurring type."""

        async def mock_refresh(obj):
            pass

        mock_db.refresh = AsyncMock(side_effect=mock_refresh)

        # Mock the add to capture the created schedule, then make refresh work
        created = {}

        def capture_add(obj):
            created["sched"] = obj

        mock_db.add = MagicMock(side_effect=capture_add)

        resp = client.post(
            "/v1/schedules",
            json={
                "name": "observe-gmail",
                "schedule_type": "recurring",
                "cron_expr": "*/15 * * * *",
                "action_type": "observe_source",
                "action_config": {"source": "gmail"},
            },
        )

        assert resp.status_code == 201
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()

    def test_create_recurring_without_cron_fails(self, client):
        """Recurring schedule without cron_expr should fail."""
        resp = client.post(
            "/v1/schedules",
            json={
                "name": "bad-schedule",
                "schedule_type": "recurring",
                "action_type": "observe_source",
            },
        )
        assert resp.status_code == 400
        assert "cron_expr required" in resp.json()["detail"]

    def test_create_one_shot_without_run_at_fails(self, client):
        """One-shot schedule without run_at should fail."""
        resp = client.post(
            "/v1/schedules",
            json={
                "name": "reminder",
                "schedule_type": "one_shot",
                "action_type": "wake_agent",
            },
        )
        assert resp.status_code == 400
        assert "run_at required" in resp.json()["detail"]

    def test_create_with_invalid_action_type(self, client):
        """Invalid action_type should fail."""
        resp = client.post(
            "/v1/schedules",
            json={
                "name": "bad",
                "schedule_type": "recurring",
                "cron_expr": "*/5 * * * *",
                "action_type": "nonexistent_action",
            },
        )
        assert resp.status_code == 400
        assert "Invalid action_type" in resp.json()["detail"]


class TestListSchedules:
    def test_list_all(self, client, mock_db):
        """GET /v1/schedules returns all user schedules."""
        sched1 = _make_db_schedule(schedule_id="sched_001", name="observe-gmail")
        sched2 = _make_db_schedule(schedule_id="sched_002", name="heartbeat")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sched1, sched2]
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/v1/schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["schedule_id"] == "sched_001"

    def test_list_filtered_by_enabled(self, client, mock_db):
        """GET /v1/schedules?enabled=true filters."""
        sched = _make_db_schedule(enabled=True)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sched]
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/v1/schedules?enabled=true")
        assert resp.status_code == 200


class TestGetSchedule:
    def test_get_existing(self, client, mock_db):
        """GET /v1/schedules/{id} returns schedule."""
        sched = _make_db_schedule()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sched
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/v1/schedules/sched_test_001")
        assert resp.status_code == 200
        assert resp.json()["schedule_id"] == "sched_test_001"

    def test_get_not_found(self, client, mock_db):
        """GET /v1/schedules/{id} returns 404 for unknown."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/v1/schedules/sched_nonexistent")
        assert resp.status_code == 404


class TestUpdateSchedule:
    def test_update_name(self, client, mock_db):
        """PATCH /v1/schedules/{id} updates fields."""
        sched = _make_db_schedule()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sched
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.refresh = AsyncMock()

        resp = client.patch(
            "/v1/schedules/sched_test_001",
            json={"name": "new-name"},
        )
        assert resp.status_code == 200
        assert sched.name == "new-name"

    def test_update_cron_recomputes_next_run(self, client, mock_db):
        """Updating cron_expr should recompute next_run_at."""
        sched = _make_db_schedule()
        old_next = sched.next_run_at
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sched
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.refresh = AsyncMock()

        resp = client.patch(
            "/v1/schedules/sched_test_001",
            json={"cron_expr": "*/5 * * * *"},
        )
        assert resp.status_code == 200
        assert sched.cron_expr == "*/5 * * * *"
        # next_run_at should have been recomputed
        assert sched.next_run_at != old_next


class TestDeleteSchedule:
    def test_delete_existing(self, client, mock_db):
        """DELETE /v1/schedules/{id} removes schedule."""
        sched = _make_db_schedule()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sched
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.delete("/v1/schedules/sched_test_001")
        assert resp.status_code == 204
        mock_db.delete.assert_awaited_once_with(sched)

    def test_delete_not_found(self, client, mock_db):
        """DELETE /v1/schedules/{id} returns 404 for unknown."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.delete("/v1/schedules/sched_nonexistent")
        assert resp.status_code == 404


class TestPauseResume:
    def test_pause(self, client, mock_db):
        """POST /v1/schedules/{id}/pause sets enabled=False."""
        sched = _make_db_schedule(enabled=True)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sched
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.refresh = AsyncMock()

        resp = client.post("/v1/schedules/sched_test_001/pause")
        assert resp.status_code == 200
        assert sched.enabled is False

    def test_resume(self, client, mock_db):
        """POST /v1/schedules/{id}/resume sets enabled=True and recomputes next_run_at."""
        sched = _make_db_schedule(enabled=False, consecutive_failures=3, last_error="some error")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sched
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.refresh = AsyncMock()

        resp = client.post("/v1/schedules/sched_test_001/resume")
        assert resp.status_code == 200
        assert sched.enabled is True
        assert sched.consecutive_failures == 0
        assert sched.last_error is None
        # next_run_at should have been recomputed
        assert sched.next_run_at is not None
