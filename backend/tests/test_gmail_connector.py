"""Tests for Gmail connector — header capture."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.connectors.gmail import GmailConnector
from tests.conftest import make_mock_settings


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
