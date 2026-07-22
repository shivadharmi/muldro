"""Tests for daily briefing generation."""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.integration_status import IntegrationStatus
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


@patch("src.services.presenter.complete_text")
@pytest.mark.asyncio
async def test_generate_briefing_creates_new(mock_complete, settings, mock_db):
    """Should generate a new briefing when none exists for the date."""
    briefing_content = {
        "headline": "2 priorities, 1 follow-up",
        "top_priorities": [{"title": "Reply to investor", "reason": "Fundraising"}],
        "changes_since_last": [{"source": "gmail", "summary": "3 new emails", "count": 3}],
        "recommended_actions": ["Review investor email"],
        "full_text": "Today you have 2 priorities...",
    }

    mock_complete.return_value = json.dumps(briefing_content)

    presenter = Presenter(settings=settings, db=mock_db)
    briefing = await presenter.generate_briefing(TEST_USER_ID, date(2026, 3, 13))

    assert briefing.briefing_id.startswith("brief_")
    assert briefing.headline == "2 priorities, 1 follow-up"
    assert briefing.briefing_date == date(2026, 3, 13)
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


@patch("src.services.presenter.complete_text")
@pytest.mark.asyncio
async def test_generate_briefing_returns_cached(mock_complete, settings, mock_db):
    """Should return existing briefing without calling Claude."""
    cached_briefing = MagicMock()
    cached_briefing.briefing_id = "brief_cached"
    cached_briefing.headline = "Cached briefing"

    cached_result = MagicMock()
    cached_result.scalar_one_or_none.return_value = cached_briefing
    mock_db.execute = AsyncMock(return_value=cached_result)

    presenter = Presenter(settings=settings, db=mock_db)
    briefing = await presenter.generate_briefing(TEST_USER_ID, date(2026, 3, 13))

    assert briefing.briefing_id == "brief_cached"
    # Claude should NOT have been called
    mock_complete.assert_not_called()


@patch("src.services.presenter.complete_text")
@pytest.mark.asyncio
async def test_generate_briefing_handles_claude_failure(mock_complete, settings, mock_db):
    """Should return a fallback briefing if Claude fails."""
    mock_complete.side_effect = RuntimeError("API down")

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


# ── Bug A: connection-aware briefing ────────────────────────────────────


def _connected(server_name: str, display_name: str, connected: bool) -> IntegrationStatus:
    return IntegrationStatus(
        server_name=server_name,
        display_name=display_name,
        provider="google",
        category="oauth",
        configured=True,
        connected=connected,
        health_status="healthy",
        enabled=True,
        install_id=f"inst_{server_name}",
        scopes=[],
    )


@patch("src.services.presenter.get_integration_statuses")
@pytest.mark.asyncio
async def test_briefing_context_includes_connected_when_zero_events(
    mock_statuses, settings, mock_db
):
    """Bug A: when integrations are connected but events=0, the LLM context must
    carry a 'connected' signal so the model does not free-associate disconnection.
    """
    mock_statuses.return_value = [
        _connected("google-workspace", "Gmail", connected=True),
        _connected("google-calendar", "Calendar", connected=True),
    ]

    presenter = Presenter(settings=settings, db=mock_db)
    # mock_db default returns no events / plans / approvals → quiet day
    context = await presenter._gather_briefing_data(
        TEST_USER_ID, date(2026, 3, 13), workspace_id="ws_test"
    )

    assert "Connected Integrations" in context
    assert "Gmail" in context
    assert "connected" in context.lower()
    # No events, but the context must make clear it's a quiet (not disconnected) day.
    assert "No events" in context


@patch("src.services.presenter.get_integration_statuses")
@pytest.mark.asyncio
async def test_briefing_context_flags_disconnected_integration(mock_statuses, settings, mock_db):
    """A genuinely disconnected integration should be surfaced as not connected
    so a 'verify integrations' suggestion is appropriate.
    """
    mock_statuses.return_value = [
        _connected("google-workspace", "Gmail", connected=True),
        _connected("slack", "Slack", connected=False),
    ]

    presenter = Presenter(settings=settings, db=mock_db)
    context = await presenter._gather_briefing_data(
        TEST_USER_ID, date(2026, 3, 13), workspace_id="ws_test"
    )

    assert "Not Connected" in context or "not connected" in context.lower()
    assert "Slack" in context


def test_briefing_prompt_scopes_verify_suggestion_to_disconnection():
    """Bug A: the schema prompt must NOT instruct the model to infer
    disconnection from a quiet day — verify-integrations is only for actually
    disconnected sources.
    """
    from src.services.presenter import BRIEFING_JSON_SCHEMA

    lowered = BRIEFING_JSON_SCHEMA.lower()
    # The prompt must reference connection status to anchor the model.
    assert "connected integrations" in lowered or "disconnected" in lowered


# ── Bug B: presenter no longer owns delivery ────────────────────────────


@patch("src.services.presenter.get_integration_statuses")
@patch("src.services.presenter.complete_text")
@pytest.mark.asyncio
async def test_presenter_does_not_notify(mock_complete, mock_statuses, settings, mock_db):
    """Bug B: presenter.generate_briefing must NOT notify — delivery is owned by
    the orchestrator path. The presenter only builds + caches the Briefing.
    """
    mock_statuses.return_value = []
    briefing_content = {
        "headline": "quiet day",
        "top_priorities": [],
        "changes_since_last": [],
        "recommended_actions": [],
        "full_text": "Nothing urgent today.",
    }
    mock_complete.return_value = json.dumps(briefing_content)

    notifier = MagicMock()
    notifier.notify = AsyncMock()

    presenter = Presenter(settings=settings, db=mock_db, notifier=notifier)
    await presenter.generate_briefing(TEST_USER_ID, date(2026, 3, 13), workspace_id="ws_test")

    notifier.notify.assert_not_called()
