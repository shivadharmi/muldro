"""Tests for Gmail connector — header capture."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.gmail import GmailConnector
from tests.conftest import TEST_USER_ID, make_mock_settings


def _make_gmail_message(
    msg_id: str = "msg_001",
    thread_id: str = "thr_001",
    snippet: str = "Hey, following up on our call.",
    labels: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    """Build a mock Gmail API message response with configurable headers."""
    if labels is None:
        labels = ["INBOX", "UNREAD"]
    default_headers = {
        "From": "alice@example.com",
        "To": "bob@example.com",
        "Cc": "carol@example.com",
        "Subject": "Follow-up",
        "Date": "Mon, 30 Mar 2026 10:00:00 -0000",
        "Message-ID": "<msg_001@mail.gmail.com>",
        "In-Reply-To": "<original@mail.gmail.com>",
        "References": "<original@mail.gmail.com> <reply1@mail.gmail.com>",
    }
    if headers is not None:
        default_headers.update(headers)

    header_list = [{"name": k, "value": v} for k, v in default_headers.items()]

    return {
        "id": msg_id,
        "threadId": thread_id,
        "snippet": snippet,
        "labelIds": labels,
        "payload": {"headers": header_list},
    }


@pytest.mark.asyncio
async def test_fetch_message_captures_reply_headers():
    """_fetch_message_as_event should capture all email threading headers."""
    connector = GmailConnector(make_mock_settings())
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _make_gmail_message()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    event = await connector._fetch_message_as_event(
        mock_client, "fake-token", "usr_test", "msg_001"
    )

    assert event is not None
    rp = event.raw_payload
    assert rp is not None

    # Gmail API message ID (NOT the RFC header)
    assert rp["message_id"] == "msg_001"
    assert rp["labels"] == ["INBOX", "UNREAD"]

    # RFC threading headers
    assert rp["rfc_message_id"] == "<msg_001@mail.gmail.com>"
    assert rp["in_reply_to"] == "<original@mail.gmail.com>"
    assert rp["references"] == "<original@mail.gmail.com> <reply1@mail.gmail.com>"

    # Recipient headers
    assert rp["to"] == "bob@example.com"
    assert rp["cc"] == "carol@example.com"


@pytest.mark.asyncio
async def test_fetch_message_detail_includes_thread_headers():
    """_fetch_message_detail should include threading headers in returned dict."""
    connector = GmailConnector(make_mock_settings())
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _make_gmail_message()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    detail = await connector._fetch_message_detail(mock_client, "fake-token", "msg_001")

    assert detail is not None
    assert detail["message_id"] == "msg_001"
    assert detail["from"] == "alice@example.com"
    assert detail["to"] == "bob@example.com"
    assert detail["subject"] == "Follow-up"

    # New threading headers
    assert detail["cc"] == "carol@example.com"
    assert detail["rfc_message_id"] == "<msg_001@mail.gmail.com>"
    assert detail["in_reply_to"] == "<original@mail.gmail.com>"
    assert detail["references"] == "<original@mail.gmail.com> <reply1@mail.gmail.com>"


@pytest.mark.asyncio
async def test_fetch_message_as_event_missing_optional_headers():
    """Missing optional headers should default to empty strings."""
    connector = GmailConnector(make_mock_settings())
    msg = _make_gmail_message(
        headers={
            "From": "alice@example.com",
            "Subject": "No threading",
            "Date": "Mon, 30 Mar 2026 10:00:00 -0000",
        },
    )
    # Remove optional headers that were set by default
    msg["payload"]["headers"] = [
        h for h in msg["payload"]["headers"] if h["name"] in ("From", "Subject", "Date")
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = msg

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    event = await connector._fetch_message_as_event(
        mock_client, "fake-token", "usr_test", "msg_001"
    )

    assert event is not None
    rp = event.raw_payload
    assert rp["message_id"] == "msg_001"
    assert rp["to"] == ""
    assert rp["cc"] == ""
    assert rp["rfc_message_id"] == ""
    assert rp["in_reply_to"] == ""
    assert rp["references"] == ""


@pytest.mark.asyncio
async def test_fetch_message_detail_missing_optional_headers():
    """_fetch_message_detail should return empty strings for absent optional headers."""
    connector = GmailConnector(make_mock_settings())

    # Build a message with only the basic required headers (no Cc, Message-ID,
    # In-Reply-To, or References).
    msg = {
        "id": "msg_002",
        "threadId": "thr_002",
        "snippet": "Just the basics.",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "bob@example.com"},
                {"name": "Subject", "value": "Plain message"},
                {"name": "Date", "value": "Mon, 30 Mar 2026 12:00:00 -0000"},
            ],
        },
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = msg

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    detail = await connector._fetch_message_detail(mock_client, "fake-token", "msg_002")

    assert detail is not None

    # Basic headers should be present
    assert detail["from"] == "alice@example.com"
    assert detail["to"] == "bob@example.com"
    assert detail["subject"] == "Plain message"
    assert detail["date"] == "Mon, 30 Mar 2026 12:00:00 -0000"

    # Optional headers should default to empty strings
    assert detail["cc"] == ""
    assert detail["rfc_message_id"] == ""
    assert detail["in_reply_to"] == ""
    assert detail["references"] == ""


@pytest.mark.asyncio
async def test_poll_expired_history_recovers_via_full_sync():
    """A 404 on history.list (expired historyId cursor) must recover via full sync.

    Gmail returns HTTP 404 from history.list when the stored historyId is older
    than ~a week. The connector must re-enter the initial full-sync path
    (cursor=None) — listing recent messages and fetching a FRESH historyId from
    the profile endpoint — NOT silently re-save the dead cursor and return [].

    Regression: before the fix, the 404 branch did a dead `cursor = None` store
    and fell through to return events=[] with the OLD expired cursor, so every
    subsequent poll 404'd forever and Gmail perception died silently.
    """
    connector = GmailConnector(make_mock_settings())

    expired_cursor = "expired_history_id_111"
    fresh_history_id = "fresh_history_id_999"

    # Response sequence over the recursive recovery path:
    #   1. history.list (incremental, original cursor) -> 404 (expired)
    #   2. recurse with cursor=None -> messages.list -> 200, one message
    #   3. _fetch_message_as_event -> message detail GET -> 200
    #   4. profile GET -> 200 with the FRESH historyId
    history_404 = MagicMock()
    history_404.status_code = 404

    messages_list_resp = MagicMock()
    messages_list_resp.status_code = 200
    messages_list_resp.json.return_value = {"messages": [{"id": "msg_001"}]}

    message_detail_resp = MagicMock()
    message_detail_resp.status_code = 200
    message_detail_resp.json.return_value = _make_gmail_message(msg_id="msg_001")

    profile_resp = MagicMock()
    profile_resp.status_code = 200
    profile_resp.json.return_value = {"historyId": fresh_history_id}

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=[
                history_404,
                messages_list_resp,
                message_detail_resp,
                profile_resp,
            ]
        )
        mock_cls.return_value = mock_client

        result = await connector.poll(TEST_USER_ID, expired_cursor, {"access_token": "tok"})

    # Recovered: the backfilled message is returned as an event ...
    assert result.ok is True
    assert len(result.events) == 1
    assert result.events[0].raw_payload["message_id"] == "msg_001"

    # ... and the cursor advances to the FRESH historyId, never the expired one.
    assert result.cursor == fresh_history_id
    assert result.cursor != expired_cursor
