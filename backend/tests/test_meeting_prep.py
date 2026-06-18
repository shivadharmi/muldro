"""Tests for meeting prep — Presenter.generate_meeting_prep and find_next_meeting."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.poll_result import PollResult
from src.services.presenter import Presenter
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings, make_raw_event


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute = AsyncMock()
    return db


def _make_meeting_event():
    event = MagicMock()
    event.event_id = "evt_cal_001"
    event.user_id = TEST_USER_ID
    event.source = "calendar"
    event.event_type = "calendar_event_created"
    event.entity_type = "calendar_event"
    event.entity_id = "cal_evt_001"
    event.occurred_at = datetime(2026, 3, 14, 10, 0, tzinfo=timezone.utc)
    event.title = "Series B Strategy Meeting"
    event.summary = "Discuss term sheet | Attendees: alice@acme.com"
    event.actor_entities = [{"type": "person", "email": "founder@startup.com", "name": "Founder"}]
    event.raw_ref = None
    event.importance_score = 0.9
    event.urgency_score = 0.8
    return event


@patch("src.services.presenter.get_anthropic_client")
@pytest.mark.asyncio
async def test_meeting_prep_not_found(mock_get_client, settings, mock_db):
    """Should return 'not found' when meeting doesn't exist."""
    # All DB queries return empty
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    empty_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=empty_result)

    presenter = Presenter(settings=settings, db=mock_db)
    result = await presenter.generate_meeting_prep("nonexistent", TEST_USER_ID)

    assert result["title"] == "Meeting not found"
    assert "Could not find" in result["risks"][0]


@patch("src.services.presenter.get_anthropic_client")
@pytest.mark.asyncio
async def test_meeting_prep_generates_content(mock_get_client, settings, mock_db):
    """Should generate meeting prep with Claude for a valid meeting."""
    meeting = _make_meeting_event()
    prep_output = {
        "agenda": ["Review term sheet", "Discuss valuation"],
        "attendee_briefs": [
            {
                "name": "Alice Chen",
                "email": "alice@acme.com",
                "role": "CFO",
                "recent_context": "Discussed Q4 financials last week",
            }
        ],
        "related_threads": [],
        "action_items": [
            {
                "description": "Prepare updated cap table",
                "owner": "Founder",
                "priority": "high",
            }
        ],
        "risks": ["Bob has not confirmed attendance"],
        "talking_points": ["Lead with traction metrics"],
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(prep_output))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    # First query: find meeting by event_id
    meeting_result = MagicMock()
    meeting_result.scalar_one_or_none.return_value = meeting

    # Subsequent queries: entities, related events, memories (all empty)
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    empty_result.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(
        side_effect=[meeting_result, empty_result, empty_result, empty_result]
    )

    presenter = Presenter(settings=settings, db=mock_db)
    result = await presenter.generate_meeting_prep("evt_cal_001", TEST_USER_ID)

    assert result["title"] == "Series B Strategy Meeting"
    assert result["meeting_id"] == "evt_cal_001"
    assert len(result["agenda"]) == 2
    assert result["agenda"][0] == "Review term sheet"
    assert len(result["action_items"]) == 1
    mock_client.messages.create.assert_called_once()


@patch("src.services.presenter.get_anthropic_client")
@pytest.mark.asyncio
async def test_meeting_prep_next_meeting(mock_get_client, settings, mock_db):
    """Should find the next upcoming meeting when next=True."""
    meeting = _make_meeting_event()
    prep_output = {
        "agenda": ["Standup"],
        "attendee_briefs": [],
        "related_threads": [],
        "action_items": [],
        "risks": [],
        "talking_points": [],
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(prep_output))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    # First query: find next upcoming meeting
    meeting_result = MagicMock()
    meeting_result.scalar_one_or_none.return_value = meeting

    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(
        side_effect=[meeting_result, empty_result, empty_result, empty_result]
    )

    presenter = Presenter(settings=settings, db=mock_db)
    result = await presenter.generate_meeting_prep(None, TEST_USER_ID, next_meeting=True)

    assert result["title"] == "Series B Strategy Meeting"


@patch("src.services.presenter.get_anthropic_client")
@pytest.mark.asyncio
async def test_meeting_prep_claude_failure(mock_get_client, settings, mock_db):
    """Should return fallback when Claude fails."""
    meeting = _make_meeting_event()

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("Claude unavailable"))
    mock_get_client.return_value = mock_client

    meeting_result = MagicMock()
    meeting_result.scalar_one_or_none.return_value = meeting

    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(
        side_effect=[meeting_result, empty_result, empty_result, empty_result]
    )

    presenter = Presenter(settings=settings, db=mock_db)
    result = await presenter.generate_meeting_prep("evt_cal_001", TEST_USER_ID)

    assert result["title"] == "Series B Strategy Meeting"
    assert "Meeting prep generation failed" in result["risks"][0]


# ---------------------------------------------------------------------------
# Regression tests: find_next_meeting fallback path must consume PollResult
# (not unpack a bare 2-tuple — that was a runtime TypeError).
# ---------------------------------------------------------------------------


def _make_calendar_raw_event():
    """A RawEvent that looks like a future calendar entry."""
    evt = make_raw_event(
        source="calendar",
        event_type="calendar_event_created",
        entity_type="calendar_event",
        entity_id="cal_001",
        title="Board sync",
        occurred_at=datetime(2099, 1, 1, 10, 0, tzinfo=timezone.utc),
    )
    return evt


@pytest.mark.asyncio
async def test_find_next_meeting_fallback_success_consumes_poll_result():
    """Fallback connector path: PollResult with events must yield the meeting."""
    from src.workflows.context import WorkflowContext
    from src.workflows.meeting_prep import find_next_meeting

    calendar_event = _make_calendar_raw_event()
    poll_result = PollResult(events=[calendar_event], cursor="tok_1", error_class="none")

    mock_connector = AsyncMock()
    mock_connector.poll = AsyncMock(return_value=poll_result)

    mock_connector_cls = MagicMock(return_value=mock_connector)

    ctx = MagicMock(spec=WorkflowContext)
    ctx.user_id = TEST_USER_ID
    ctx.workspace_id = TEST_WORKSPACE_ID
    # settings must be truthy so the connector is constructed via cal_cls(settings)
    # rather than the bare cal_cls.__new__(cal_cls) fallback
    ctx.settings = MagicMock()
    ctx.credentials = {}

    with (
        patch("src.workflows.meeting_prep._poll_calendar_via_mcp", AsyncMock(return_value=None)),
        patch(
            "src.connectors.base.CONNECTOR_REGISTRY",
            {"calendar": mock_connector_cls},
        ),
    ):
        result = await find_next_meeting(ctx)

    assert result.get("source") == "connector"
    assert result["meeting"]["title"] == "Board sync"
    assert result["meeting"]["entity_id"] == "cal_001"


@pytest.mark.asyncio
async def test_find_next_meeting_fallback_failed_poll_result_returns_empty():
    """Fallback connector path: failed PollResult must degrade gracefully (no TypeError)."""
    from src.workflows.context import WorkflowContext
    from src.workflows.meeting_prep import find_next_meeting

    failed_result = PollResult(events=[], cursor=None, error_class="transient")

    mock_connector = AsyncMock()
    mock_connector.poll = AsyncMock(return_value=failed_result)
    mock_connector_cls = MagicMock(return_value=mock_connector)

    ctx = MagicMock(spec=WorkflowContext)
    ctx.user_id = TEST_USER_ID
    ctx.workspace_id = TEST_WORKSPACE_ID
    ctx.settings = MagicMock()
    ctx.credentials = {}

    with (
        patch("src.workflows.meeting_prep._poll_calendar_via_mcp", AsyncMock(return_value=None)),
        patch(
            "src.connectors.base.CONNECTOR_REGISTRY",
            {"calendar": mock_connector_cls},
        ),
    ):
        result = await find_next_meeting(ctx)

    # Must not raise; should return a no-meeting sentinel
    assert result.get("meeting") is None
