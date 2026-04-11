"""Tests for daily briefing generation."""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.presenter import Presenter
from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    # Default: no cached briefing, no events, no plans, no approvals
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    no_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=no_result)
    return db


@patch("src.services.presenter.get_anthropic_client")
@pytest.mark.asyncio
async def test_generate_briefing_creates_new(mock_get_client, settings, mock_db):
    """Should generate a new briefing when none exists for the date."""
    briefing_content = {
        "headline": "2 priorities, 1 follow-up",
        "top_priorities": [{"title": "Reply to investor", "reason": "Fundraising"}],
        "changes_since_last": [{"source": "gmail", "summary": "3 new emails", "count": 3}],
        "recommended_actions": ["Review investor email"],
        "full_text": "Today you have 2 priorities...",
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(briefing_content))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    presenter = Presenter(settings=settings, db=mock_db)
    briefing = await presenter.generate_briefing(TEST_USER_ID, date(2026, 3, 13))

    assert briefing.briefing_id.startswith("brief_")
    assert briefing.headline == "2 priorities, 1 follow-up"
    assert briefing.briefing_date == date(2026, 3, 13)
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


@patch("src.services.presenter.get_anthropic_client")
@pytest.mark.asyncio
async def test_generate_briefing_returns_cached(mock_get_client, settings, mock_db):
    """Should return existing briefing without calling Claude."""
    cached_briefing = MagicMock()
    cached_briefing.briefing_id = "brief_cached"
    cached_briefing.headline = "Cached briefing"

    cached_result = MagicMock()
    cached_result.scalar_one_or_none.return_value = cached_briefing
    mock_db.execute = AsyncMock(return_value=cached_result)

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    presenter = Presenter(settings=settings, db=mock_db)
    briefing = await presenter.generate_briefing(TEST_USER_ID, date(2026, 3, 13))

    assert briefing.briefing_id == "brief_cached"
    # Claude should NOT have been called
    mock_client.messages.create.assert_not_called()


@patch("src.services.presenter.get_anthropic_client")
@pytest.mark.asyncio
async def test_generate_briefing_handles_claude_failure(mock_get_client, settings, mock_db):
    """Should return a fallback briefing if Claude fails."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
    mock_get_client.return_value = mock_client

    presenter = Presenter(settings=settings, db=mock_db)
    briefing = await presenter.generate_briefing(TEST_USER_ID, date(2026, 3, 13))

    assert briefing.headline == "Unable to generate briefing"
    assert briefing.briefing_id.startswith("brief_")


def test_briefing_endpoint_returns_response():
    """The briefing endpoint should return a valid BriefingResponse."""
    from src.models.briefings import Briefing

    mock_briefing = MagicMock(spec=Briefing)
    mock_briefing.briefing_id = "brief_test"
    mock_briefing.briefing_date = date(2026, 3, 13)
    mock_briefing.headline = "Test headline"
    mock_briefing.top_priorities = [{"title": "Test"}]
    mock_briefing.changes_since_last = []
    mock_briefing.pending_approvals = []
    mock_briefing.recommended_actions = ["Do something"]
    mock_briefing.full_text = "Test briefing"

    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_briefing
    mock_db.execute = AsyncMock(return_value=mock_result)

    from fastapi.testclient import TestClient

    from src.api.app import app
    from src.api.deps import get_current_user, get_current_user_id, get_session

    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[get_session] = lambda: mock_db
    try:
        client = TestClient(app)
        response = client.get("/v1/briefings/2026-03-13")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    data = response.json()
    assert data["briefing_id"] == "brief_test"
    assert data["headline"] == "Test headline"
