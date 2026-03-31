"""Tests for full thread context fetching on email replies."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.event_processor import RawEvent


def _make_reply_event(thread_id="thr_001", message_id="msg_002"):
    return RawEvent(
        source="gmail",
        source_account_id="gmail_primary",
        event_type="email_received",
        entity_type="email_thread",
        entity_id=thread_id,
        title="Re: Investment proposal",
        summary="Can you provide an update on this?",
        actor={"type": "person", "email": "investor@fund.com", "name": "Investor"},
        raw_payload={
            "message_id": message_id,
            "in_reply_to": "<msg_001@mail.gmail.com>",
            "references": "<msg_001@mail.gmail.com>",
            "rfc_message_id": f"<{message_id}@mail.gmail.com>",
            "to": "user@example.com",
            "cc": "",
            "labels": ["INBOX"],
        },
    )


def _make_new_email_event(thread_id="thr_002"):
    return RawEvent(
        source="gmail",
        source_account_id="gmail_primary",
        event_type="email_received",
        entity_type="email_thread",
        entity_id=thread_id,
        title="New project proposal",
        summary="We'd like to propose a new initiative.",
        actor={"type": "person", "email": "partner@example.com", "name": "Partner"},
        raw_payload={
            "message_id": "msg_new_001",
            "in_reply_to": "",
            "references": "",
            "rfc_message_id": "<msg_new_001@mail.gmail.com>",
            "to": "user@example.com",
            "cc": "",
            "labels": ["INBOX"],
        },
    )


@pytest.mark.asyncio
async def test_fetch_thread_context_for_replies():
    """When raw_events contain a reply, thread context should be fetched."""
    from src.orchestrator.jarvis import _fetch_thread_contexts

    mock_thread_result = {
        "status": "ok",
        "messages": [
            {"from": "user@example.com", "snippet": "Here is the investment proposal."},
            {"from": "investor@fund.com", "snippet": "Can you provide an update on this?"},
        ],
    }

    with patch("src.connectors.mcp_bridge.call_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        with patch("src.connectors.mcp_bridge.is_mcp_tool", return_value=True):
            mock_mcp.return_value = mock_thread_result
            raw_events = [_make_reply_event("thr_001"), _make_new_email_event("thr_002")]
            contexts = await _fetch_thread_contexts(
                raw_events, user_id="usr_test", workspace_id="ws_test"
            )

    assert "thr_001" in contexts
    assert "thr_002" not in contexts
    mock_mcp.assert_called_once()
    call_args = mock_mcp.call_args
    assert call_args[0][0] == "get_gmail_thread_content"
    assert call_args[0][1]["thread_id"] == "thr_001"


@pytest.mark.asyncio
async def test_fetch_thread_context_skips_non_gmail():
    """Non-Gmail events should not trigger thread fetch."""
    from src.orchestrator.jarvis import _fetch_thread_contexts

    slack_event = RawEvent(
        source="slack",
        source_account_id="slack_primary",
        event_type="message_posted",
        entity_type="channel",
        entity_id="ch_001",
        raw_payload={"in_reply_to": "some_thread"},
    )

    with patch("src.connectors.mcp_bridge.call_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        with patch("src.connectors.mcp_bridge.is_mcp_tool", return_value=True):
            contexts = await _fetch_thread_contexts(
                [slack_event], user_id="usr_test", workspace_id="ws_test"
            )

    assert len(contexts) == 0
    mock_mcp.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_thread_context_failure_returns_empty():
    """MCP tool failure should return empty dict, not crash."""
    from src.orchestrator.jarvis import _fetch_thread_contexts

    with patch("src.connectors.mcp_bridge.call_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        with patch("src.connectors.mcp_bridge.is_mcp_tool", return_value=True):
            mock_mcp.side_effect = RuntimeError("MCP server down")
            contexts = await _fetch_thread_contexts(
                [_make_reply_event()], user_id="usr_test", workspace_id="ws_test"
            )

    assert len(contexts) == 0
