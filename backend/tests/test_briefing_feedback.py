"""Tests for briefing feedback endpoints — learning loop."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_current_user_id, get_session
from tests.conftest import TEST_USER_ID


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
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


def _mock_briefing_exists(mock_db):
    """Configure mock_db to return a briefing ID for existence check."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = "brief_test_001"
    mock_db.execute.return_value = result


def _mock_briefing_not_found(mock_db):
    """Configure mock_db to return no briefing."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = result


class TestSubmitFeedback:
    def test_submit_rating(self, client, mock_db):
        _mock_briefing_exists(mock_db)
        resp = client.post(
            "/v1/briefings/brief_test_001/feedback",
            json={
                "feedback_type": "rating",
                "rating": 4,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["briefing_id"] == "brief_test_001"
        assert data["feedback_type"] == "rating"
        assert data["status"] == "recorded"
        assert data["feedback_id"].startswith("bfb_")
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_submit_item_acted_on(self, client, mock_db):
        _mock_briefing_exists(mock_db)
        resp = client.post(
            "/v1/briefings/brief_test_001/feedback",
            json={
                "feedback_type": "item_acted_on",
                "item_section": "recommended_actions",
                "item_index": 0,
                "item_title": "Reply to investor email",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["feedback_type"] == "item_acted_on"

    def test_submit_item_dismissed(self, client, mock_db):
        _mock_briefing_exists(mock_db)
        resp = client.post(
            "/v1/briefings/brief_test_001/feedback",
            json={
                "feedback_type": "item_dismissed",
                "item_section": "top_priorities",
                "item_title": "Newsletter roundup",
            },
        )
        assert resp.status_code == 200

    def test_submit_follow_up_asked(self, client, mock_db):
        _mock_briefing_exists(mock_db)
        resp = client.post(
            "/v1/briefings/brief_test_001/feedback",
            json={
                "feedback_type": "follow_up_asked",
                "item_section": "top_priorities",
                "item_title": "Investor email from John",
                "comment": "What did he say exactly?",
            },
        )
        assert resp.status_code == 200

    def test_reject_invalid_feedback_type(self, client, mock_db):
        _mock_briefing_exists(mock_db)
        resp = client.post(
            "/v1/briefings/brief_test_001/feedback",
            json={
                "feedback_type": "invalid_type",
            },
        )
        # Pydantic Literal constraint returns 422 for invalid enum values
        assert resp.status_code == 422

    def test_reject_rating_without_value(self, client, mock_db):
        _mock_briefing_exists(mock_db)
        resp = client.post(
            "/v1/briefings/brief_test_001/feedback",
            json={
                "feedback_type": "rating",
            },
        )
        assert resp.status_code == 400
        assert "rating" in resp.json()["error"]["message"]

    def test_reject_rating_out_of_range(self, client, mock_db):
        _mock_briefing_exists(mock_db)
        resp = client.post(
            "/v1/briefings/brief_test_001/feedback",
            json={
                "feedback_type": "rating",
                "rating": 6,
            },
        )
        # Pydantic Field(ge=1, le=5) constraint returns 422 for out-of-range ratings
        assert resp.status_code == 422

    def test_briefing_not_found(self, client, mock_db):
        _mock_briefing_not_found(mock_db)
        resp = client.post(
            "/v1/briefings/brief_nonexistent/feedback",
            json={
                "feedback_type": "rating",
                "rating": 5,
            },
        )
        assert resp.status_code == 404


class TestFeedbackSummary:
    def test_get_summary(self, client, mock_db):
        row = MagicMock()
        row.total = 5
        row.avg_rating = 4.2
        row.acted = 2
        row.dismissed = 1
        row.follow_ups = 1
        result = MagicMock()
        result.one.return_value = row
        mock_db.execute.return_value = result

        resp = client.get("/v1/briefings/brief_test_001/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert data["briefing_id"] == "brief_test_001"
        assert data["total_feedback"] == 5
        assert data["average_rating"] == 4.2
        assert data["items_acted_on"] == 2
        assert data["items_dismissed"] == 1
        assert data["follow_ups_asked"] == 1

    def test_get_summary_empty(self, client, mock_db):
        row = MagicMock()
        row.total = 0
        row.avg_rating = None
        row.acted = 0
        row.dismissed = 0
        row.follow_ups = 0
        result = MagicMock()
        result.one.return_value = row
        mock_db.execute.return_value = result

        resp = client.get("/v1/briefings/brief_test_001/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_feedback"] == 0
        assert data["average_rating"] is None
