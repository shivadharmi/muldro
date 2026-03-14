"""Tests for OpenClawClient — backend-to-OpenClaw communication via /v1/chat/completions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.openclaw_client import OpenClawClient
from tests.conftest import make_mock_settings

CHAT_RESPONSE = {
    "id": "chatcmpl_test",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "PONG"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


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
async def test_run_agent_turn_posts_to_chat_completions(mock_async_client_cls, oc_client):
    """run_agent_turn should POST to /v1/chat/completions."""
    mock_client = _mock_httpx_client(CHAT_RESPONSE)
    mock_async_client_cls.return_value = mock_client

    result = await oc_client.run_agent_turn("Draft an email to Bob")

    assert result == CHAT_RESPONSE
    call_args = mock_client.post.call_args
    assert "/v1/chat/completions" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["model"] == "openclaw:main"
    assert payload["messages"][0]["content"] == "Draft an email to Bob"
    assert payload["stream"] is False


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_run_agent_turn_custom_agent_id(mock_async_client_cls, oc_client):
    """run_agent_turn should use the specified agent ID in the model field."""
    mock_client = _mock_httpx_client(CHAT_RESPONSE)
    mock_async_client_cls.return_value = mock_client

    await oc_client.run_agent_turn("test", agent_id="ops")

    payload = mock_client.post.call_args[1]["json"]
    assert payload["model"] == "openclaw:ops"


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_run_agent_turn_includes_auth_header(mock_async_client_cls, oc_client):
    """run_agent_turn should include Authorization header when token is set."""
    mock_client = _mock_httpx_client(CHAT_RESPONSE)
    mock_async_client_cls.return_value = mock_client

    await oc_client.run_agent_turn("test")

    headers = mock_client.post.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer test-gateway-token"


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_wake_agent_uses_chat_completions(mock_async_client_cls, oc_client):
    """wake_agent should delegate to run_agent_turn (chat completions)."""
    mock_client = _mock_httpx_client(CHAT_RESPONSE)
    mock_async_client_cls.return_value = mock_client

    result = await oc_client.wake_agent("Daily briefing ready")

    assert result == CHAT_RESPONSE
    call_args = mock_client.post.call_args
    assert "/v1/chat/completions" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["messages"][0]["content"] == "Daily briefing ready"


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_delegate_task_builds_message(mock_async_client_cls, oc_client):
    """delegate_task should compose instructions and call run_agent_turn."""
    mock_client = _mock_httpx_client(CHAT_RESPONSE)
    mock_async_client_cls.return_value = mock_client

    await oc_client.delegate_task(
        task_type="send_email",
        instructions="Reply to investor about the deck",
        context={"recipient": "investor@fund.com", "tone": "professional"},
    )

    payload = mock_client.post.call_args[1]["json"]
    content = payload["messages"][0]["content"]
    assert "send_email" in content
    assert "investor@fund.com" in content
    assert "professional" in content


@pytest.mark.asyncio
@patch("src.services.openclaw_client.httpx.AsyncClient")
async def test_no_token_omits_auth_header(mock_async_client_cls):
    """Client without a token should not include Authorization header."""
    settings = make_mock_settings(openclaw_gateway_token="")
    client = OpenClawClient(settings=settings)

    mock_client = _mock_httpx_client(CHAT_RESPONSE)
    mock_async_client_cls.return_value = mock_client

    await client.run_agent_turn("test")

    headers = mock_client.post.call_args[1]["headers"]
    assert "Authorization" not in headers
