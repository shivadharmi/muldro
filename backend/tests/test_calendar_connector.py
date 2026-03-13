"""Tests for Calendar Connector — event normalization and push handling."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.calendar import CalendarConnector, CalendarEventPayload
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    return MagicMock()


def _make_calendar_event(**overrides) -> CalendarEventPayload:
    defaults = dict(
        calendar_event_id="cal_evt_001",
        calendar_id="primary",
        title="Series B Strategy Meeting",
        description="Discuss term sheet with investors",
        location="Conference Room A",
        start_time=datetime(2026, 3, 14, 10, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 3, 14, 11, 0, tzinfo=timezone.utc),
        attendees=[
            {
                "email": "alice@acme.com",
                "name": "Alice Chen",
                "response_status": "accepted",
                "organizer": False,
            },
            {
                "email": "bob@fund.com",
                "name": "Bob Investor",
                "response_status": "tentative",
                "organizer": False,
            },
        ],
        organizer_email="founder@startup.com",
        status="confirmed",
    )
    defaults.update(overrides)
    return CalendarEventPayload(**defaults)


def test_calendar_event_to_raw_event(settings, mock_db):
    """CalendarConnector should normalize a CalendarEventPayload to RawEvent."""
    mock_processor = MagicMock()
    connector = CalendarConnector(settings=settings, db=mock_db, event_processor=mock_processor)
    evt = _make_calendar_event()
    raw = connector._event_to_raw_event(evt, "calendar_test")

    assert raw.source == "calendar"
    assert raw.event_type == "calendar_event_created"
    assert raw.entity_type == "calendar_event"
    assert raw.entity_id == "cal_evt_001"
    assert raw.title == "Series B Strategy Meeting"
    assert "Alice Chen" in raw.summary
    assert "Bob Investor" in raw.summary
    assert "Conference Room A" in raw.summary
    assert raw.actor["email"] == "founder@startup.com"


@patch("src.connectors.calendar.EventProcessor")
@pytest.mark.asyncio
async def test_handle_push_notification(mock_ep_cls, settings, mock_db):
    """CalendarConnector should process push with multiple events."""
    mock_processor = MagicMock()
    mock_processor.process = AsyncMock(side_effect=["evt_001", "evt_002"])

    connector = CalendarConnector(settings=settings, db=mock_db, event_processor=mock_processor)

    payload = {
        "account_id": "cal_primary",
        "events": [
            _make_calendar_event(calendar_event_id="cal_001").model_dump(mode="json"),
            _make_calendar_event(
                calendar_event_id="cal_002",
                title="Team Standup",
            ).model_dump(mode="json"),
        ],
    }

    event_ids = await connector.handle_push_notification(payload, "usr_default")

    assert len(event_ids) == 2
    assert mock_processor.process.call_count == 2


@patch("src.connectors.calendar.EventProcessor")
@pytest.mark.asyncio
async def test_process_test_event(mock_ep_cls, settings, mock_db):
    """CalendarConnector should process a single test event."""
    mock_processor = MagicMock()
    mock_processor.process = AsyncMock(return_value="evt_cal_001")

    connector = CalendarConnector(settings=settings, db=mock_db, event_processor=mock_processor)

    evt = _make_calendar_event()
    event_id = await connector.process_test_event(evt, "usr_default")

    assert event_id == "evt_cal_001"
    mock_processor.process.assert_called_once()
    raw = mock_processor.process.call_args[0][0]
    assert raw.source == "calendar"


def test_event_without_attendees(settings, mock_db):
    """CalendarConnector should handle events with no attendees."""
    mock_processor = MagicMock()
    connector = CalendarConnector(settings=settings, db=mock_db, event_processor=mock_processor)
    evt = _make_calendar_event(attendees=[], organizer_email=None)
    raw = connector._event_to_raw_event(evt, "calendar_test")

    assert raw.actor is None
    assert raw.title == "Series B Strategy Meeting"
