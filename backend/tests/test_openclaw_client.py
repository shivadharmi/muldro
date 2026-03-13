"""Tests for OpenClawClient — backend-to-OpenClaw communication."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.openclaw_client import OpenClawClient
from tests.conftest import make_mock_settings


def _mock_httpx_client(response_data: dict):
    """Create a mock httpx.AsyncClient that returns the given response data."""
    mock_response = MagicMock()
    mock_response.json.return_value = response_data
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.fixture
def oc_client():
    settings = make_mock_settings()
    return OpenClawClient(settings=settings)


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_wake_agent_posts_to_hooks_wake(mock_async_client_cls, oc_client):
    """wake_agent should POST to /hooks/wake with message and session key."""
    mock_client = _mock_httpx_client({"ok": True})
    mock_async_client_cls.return_value = mock_client

    result = await oc_client.wake_agent("Daily briefing ready")

    assert result == {"ok": True}
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "/hooks/wake" in call_args[0][0]
    assert call_args[1]["json"]["message"] == "Daily briefing ready"
    assert call_args[1]["json"]["sessionKey"] == "hook:jarvis"


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_wake_agent_includes_auth_header(mock_async_client_cls, oc_client):
    """wake_agent should include Authorization header when token is set."""
    mock_client = _mock_httpx_client({"ok": True})
    mock_async_client_cls.return_value = mock_client

    await oc_client.wake_agent("test")

    headers = mock_client.post.call_args[1]["headers"]
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer test-hook-token"


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_run_agent_turn_posts_to_hooks_agent(mock_async_client_cls, oc_client):
    """run_agent_turn should POST to /hooks/agent."""
    mock_client = _mock_httpx_client({"response": "done"})
    mock_async_client_cls.return_value = mock_client

    result = await oc_client.run_agent_turn("Draft an email to Bob")

    assert result == {"response": "done"}
    call_args = mock_client.post.call_args
    assert "/hooks/agent" in call_args[0][0]
    assert call_args[1]["json"]["message"] == "Draft an email to Bob"
    assert call_args[1]["json"]["agentId"] == "jarvis"


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_delegate_task_builds_message_and_calls_agent(mock_async_client_cls, oc_client):
    """delegate_task should compose instructions and call run_agent_turn."""
    mock_client = _mock_httpx_client({"result": "email sent"})
    mock_async_client_cls.return_value = mock_client

    result = await oc_client.delegate_task(
        task_type="send_email",
        instructions="Reply to investor about the deck",
        context={"recipient": "investor@fund.com", "tone": "professional"},
    )

    assert result == {"result": "email sent"}
    # Verify the composed message includes task details
    call_args = mock_client.post.call_args
    message = call_args[1]["json"]["message"]
    assert "send_email" in message
    assert "investor@fund.com" in message
    assert "professional" in message


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_wake_agent_no_token(mock_async_client_cls):
    """wake_agent should work without a token (no auth header)."""
    settings = make_mock_settings(openclaw_hook_token="")
    client = OpenClawClient(settings=settings)

    mock_client = _mock_httpx_client({"ok": True})
    mock_async_client_cls.return_value = mock_client

    await client.wake_agent("test")

    headers = mock_client.post.call_args[1]["headers"]
    assert "Authorization" not in headers


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_run_agent_turn_with_deliver(mock_async_client_cls, oc_client):
    """run_agent_turn should include deliver param when specified."""
    mock_client = _mock_httpx_client({"response": "delivered"})
    mock_async_client_cls.return_value = mock_client

    await oc_client.run_agent_turn("Briefing text", deliver="slack")

    call_args = mock_client.post.call_args
    assert call_args[1]["json"]["deliver"] == "slack"
