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
