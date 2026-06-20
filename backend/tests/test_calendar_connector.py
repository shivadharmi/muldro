"""Tests for the Google Calendar connector — pagination + syncToken handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.calendar import CalendarConnector
from tests.conftest import TEST_USER_ID, make_mock_settings


def _make_calendar_event(event_id: str, summary: str = "Meeting") -> dict:
    """Build a minimal Google Calendar event item."""
    return {
        "id": event_id,
        "status": "confirmed",
        "summary": summary,
        "start": {"dateTime": "2026-06-21T10:00:00Z"},
        "end": {"dateTime": "2026-06-21T11:00:00Z"},
        "organizer": {"email": "alice@example.com", "displayName": "Alice"},
        "attendees": [],
    }


@pytest.mark.asyncio
async def test_calendar_paginates_and_takes_synctoken_from_last_page():
    """poll() must follow nextPageToken and take nextSyncToken from the final page.

    Google Calendar returns ``nextSyncToken`` ONLY on the last page; intermediate
    pages carry ``nextPageToken``. Before the fix, the connector issued a single GET:
    multi-page results dropped events past page 1 AND lost the sync token, so
    ``new_cursor`` fell back to the old cursor and the connector got stuck
    re-fetching the same first page forever.
    """
    connector = CalendarConnector(make_mock_settings())

    incoming_cursor = "sync_token_start"
    page2_sync_token = "sync_token_final_999"

    # Page 1: two events + nextPageToken, NO nextSyncToken.
    page1 = MagicMock()
    page1.status_code = 200
    page1.json.return_value = {
        "items": [
            _make_calendar_event("evt_1", "Standup"),
            _make_calendar_event("evt_2", "Design review"),
        ],
        "nextPageToken": "page2tok",
    }
    # Page 2 (final): one more event + nextSyncToken, NO nextPageToken.
    page2 = MagicMock()
    page2.status_code = 200
    page2.json.return_value = {
        "items": [_make_calendar_event("evt_3", "Retro")],
        "nextSyncToken": page2_sync_token,
    }

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[page1, page2])
        mock_cls.return_value = mock_client

        result = await connector.poll(TEST_USER_ID, incoming_cursor, {"access_token": "tok"})

    assert result.ok is True

    # ALL events across both pages are collected.
    ingested = {e.entity_id for e in result.events}
    assert ingested == {"evt_1", "evt_2", "evt_3"}

    # The cursor advances to the nextSyncToken from the FINAL page, never the old one.
    assert result.cursor == page2_sync_token
    assert result.cursor != incoming_cursor

    # The first request carried the incremental syncToken; the second carried ONLY
    # pageToken (combining pageToken with syncToken/timeMin is rejected by the API).
    first_params = mock_client.get.call_args_list[0].kwargs["params"]
    second_params = mock_client.get.call_args_list[1].kwargs["params"]
    assert first_params.get("syncToken") == incoming_cursor
    assert "pageToken" not in first_params
    assert second_params.get("pageToken") == "page2tok"
    assert "syncToken" not in second_params
    assert "timeMin" not in second_params


def _resp(status_code: int, payload: dict | None = None) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    resp.text = ""
    return resp


def _patched_client(side_effect):
    """Build a patched httpx.AsyncClient whose get() yields the given responses."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=side_effect)
    return mock_client


@pytest.mark.asyncio
async def test_calendar_happy_path_normalization():
    """A single 200 page normalizes to RawEvents and advances the cursor to nextSyncToken."""
    connector = CalendarConnector(make_mock_settings())

    page = _resp(
        200,
        {
            "items": [_make_calendar_event("evt_42", "Sync")],
            "nextSyncToken": "fresh_sync_token",
        },
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _patched_client([page])
        result = await connector.poll(TEST_USER_ID, "old_cursor", {"access_token": "tok"})

    assert result.ok is True
    assert len(result.events) == 1
    event = result.events[0]
    assert event.source == "calendar"
    assert event.entity_id == "evt_42"
    assert event.entity_type == "meeting"
    # A confirmed event maps to event_created.
    assert event.event_type == "event_created"
    assert event.title == "Sync"
    assert event.actor["email"] == "alice@example.com"
    # occurred_at parsed from start.dateTime (Z suffix handled).
    assert event.occurred_at is not None
    assert event.occurred_at.tzinfo is not None
    # Cursor advances to the page's nextSyncToken.
    assert result.cursor == "fresh_sync_token"


@pytest.mark.asyncio
async def test_calendar_cancelled_event_maps_to_cancellation():
    """A cancelled event (status=cancelled, no start/end) → event_cancelled, no crash."""
    connector = CalendarConnector(make_mock_settings())

    cancelled = {"id": "evt_gone", "status": "cancelled"}
    page = _resp(200, {"items": [cancelled], "nextSyncToken": "tok_after"})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _patched_client([page])
        result = await connector.poll(TEST_USER_ID, "c", {"access_token": "tok"})

    assert result.ok is True
    assert len(result.events) == 1
    event = result.events[0]
    assert event.entity_id == "evt_gone"
    assert event.event_type == "event_cancelled"
    # No start time → occurred_at stays None, no crash.
    assert event.occurred_at is None


@pytest.mark.asyncio
async def test_calendar_all_day_event_does_not_crash():
    """An all-day event (start.date instead of start.dateTime) parses occurred_at, no crash."""
    connector = CalendarConnector(make_mock_settings())

    all_day = {
        "id": "evt_allday",
        "status": "confirmed",
        "summary": "Company holiday",
        "start": {"date": "2026-06-25"},
        "end": {"date": "2026-06-26"},
    }
    page = _resp(200, {"items": [all_day], "nextSyncToken": "tok_after"})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _patched_client([page])
        result = await connector.poll(TEST_USER_ID, "c", {"access_token": "tok"})

    assert result.ok is True
    assert len(result.events) == 1
    event = result.events[0]
    assert event.entity_id == "evt_allday"
    # A date-only start is a valid ISO date → occurred_at parses (date midnight).
    assert event.occurred_at is not None
    assert event.occurred_at.year == 2026
    assert event.occurred_at.month == 6
    assert event.occurred_at.day == 25


@pytest.mark.asyncio
async def test_calendar_empty_calendar_advances_cursor():
    """An empty calendar (items=[], nextSyncToken present) → ok, no events, cursor advances."""
    connector = CalendarConnector(make_mock_settings())

    page = _resp(200, {"items": [], "nextSyncToken": "empty_sync_token"})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _patched_client([page])
        result = await connector.poll(TEST_USER_ID, "old", {"access_token": "tok"})

    assert result.ok is True
    assert result.events == []
    assert result.cursor == "empty_sync_token"


@pytest.mark.asyncio
async def test_calendar_410_triggers_full_resync():
    """410-GONE → full re-sync: second call drops syncToken, sends timeMin; bounded recursion."""
    connector = CalendarConnector(make_mock_settings())

    gone = _resp(410)
    # The re-sync (cursor=None) hits the first-sync branch: timeMin, no syncToken,
    # and a final nextSyncToken so the cursor recovers.
    resync = _resp(
        200,
        {"items": [_make_calendar_event("evt_after", "Recovered")], "nextSyncToken": "fresh"},
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = _patched_client([gone, resync])
        mock_cls.return_value = mock_client
        result = await connector.poll(TEST_USER_ID, "expired_sync", {"access_token": "tok"})

    assert result.ok is True
    assert {e.entity_id for e in result.events} == {"evt_after"}
    # Recovered with the fresh token, not stuck on the expired one.
    assert result.cursor == "fresh"
    # Exactly two GETs: the original (410) + the single re-sync. No infinite recursion.
    assert mock_client.get.await_count == 2
    second_params = mock_client.get.call_args_list[1].kwargs["params"]
    assert "syncToken" not in second_params
    assert "timeMin" in second_params


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,expected_class",
    [
        (403, "auth_failed"),
        (429, "rate_limited"),
        (500, "transient"),
    ],
)
async def test_calendar_http_error_classes(status_code, expected_class):
    """Non-200/410 responses map to the right error_class; cursor unchanged."""
    connector = CalendarConnector(make_mock_settings())
    incoming_cursor = "keep_me"

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _patched_client([_resp(status_code)])
        result = await connector.poll(TEST_USER_ID, incoming_cursor, {"access_token": "tok"})

    assert result.failed is True
    assert result.error_class == expected_class
    assert result.cursor == incoming_cursor
    assert result.events == []


@pytest.mark.asyncio
async def test_calendar_first_poll_sends_timemin_not_synctoken():
    """First poll (cursor=None) sends timeMin and omits syncToken."""
    connector = CalendarConnector(make_mock_settings())

    page = _resp(200, {"items": [], "nextSyncToken": "first_token"})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = _patched_client([page])
        mock_cls.return_value = mock_client
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    assert result.cursor == "first_token"
    params = mock_client.get.call_args_list[0].kwargs["params"]
    assert "timeMin" in params
    assert "syncToken" not in params


@pytest.mark.asyncio
async def test_calendar_missing_access_token_is_auth_failed():
    """Missing access_token → auth_failed, cursor preserved, no HTTP call."""
    connector = CalendarConnector(make_mock_settings())

    result = await connector.poll(TEST_USER_ID, "cursor_x", {})

    assert result.error_class == "auth_failed"
    assert result.cursor == "cursor_x"
    assert result.events == []
