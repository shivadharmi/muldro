"""Tests for GraphExecutor._draft_action thread_id passthrough."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


@pytest.fixture
def mock_settings():
    settings = make_mock_settings()
    settings.resolved_model = "claude-sonnet-4-20250514"
    return settings


@pytest.fixture
def mock_anthropic_client():
    """Mock Anthropic client that returns a valid draft JSON response."""
    client = MagicMock()
    response = MagicMock()
    content_block = MagicMock()
    content_block.text = json.dumps(
        {
            "subject": "Re: Follow-up",
            "body": "Hello, thanks for the update.",
            "tone": "professional",
        }
    )
    response.content = [content_block]
    response.usage = MagicMock(input_tokens=50, output_tokens=100)
    client.messages.create = AsyncMock(return_value=response)
    return client


@pytest.fixture
def mock_connector():
    """Mock Gmail connector that records execute_action calls."""
    connector = MagicMock()
    connector.execute_action = AsyncMock(return_value={"draft_id": "draft_xyz789"})
    return connector


@pytest.fixture
def mock_run():
    run = MagicMock()
    run.run_id = "run_01JTESTDRAFT000000000000000"
    run.user_id = TEST_USER_ID
    run.workspace_id = TEST_WORKSPACE_ID
    return run


def _make_executor(mock_settings, mock_anthropic_client):
    """Build a GraphExecutor with mocked Anthropic client."""
    from src.services.graph_executor import GraphExecutor

    creds_fn = AsyncMock(return_value={"access_token": "tok_test"})
    with patch(
        "src.services.graph_executor.get_anthropic_client",
        return_value=mock_anthropic_client,
    ):
        executor = GraphExecutor(
            settings=mock_settings,
            db=AsyncMock(),
            connector_credentials_fn=creds_fn,
        )
    return executor


@pytest.mark.asyncio
async def test_draft_passes_thread_id(
    mock_settings, mock_anthropic_client, mock_connector, mock_run
):
    """When input_data contains thread_id, it should be passed to create_draft."""
    executor = _make_executor(mock_settings, mock_anthropic_client)
    connector_cls = MagicMock(return_value=mock_connector)

    with patch("src.connectors.base.CONNECTOR_REGISTRY", {"gmail": connector_cls}):
        result = await executor._draft_action(
            input_data={
                "recipient": "alice@example.com",
                "goal": "Reply to investor thread",
                "thread_id": "thr_abc123",
            },
            run=mock_run,
        )

    # Verify connector was called with thread_id in the params
    mock_connector.execute_action.assert_called_once()
    call_args = mock_connector.execute_action.call_args
    action_name = call_args[0][0]
    params = call_args[0][1]

    assert action_name == "create_draft"
    assert params["to"] == "alice@example.com"
    assert params["thread_id"] == "thr_abc123"
    assert result["draft"]["created_in_gmail"] is True


@pytest.mark.asyncio
async def test_draft_works_without_thread_id(
    mock_settings, mock_anthropic_client, mock_connector, mock_run
):
    """When input_data has no thread_id, create_draft should NOT include thread_id."""
    executor = _make_executor(mock_settings, mock_anthropic_client)
    connector_cls = MagicMock(return_value=mock_connector)

    with patch("src.connectors.base.CONNECTOR_REGISTRY", {"gmail": connector_cls}):
        result = await executor._draft_action(
            input_data={
                "recipient": "bob@example.com",
                "goal": "Send a cold outreach email",
            },
            run=mock_run,
        )

    # Verify connector was called WITHOUT thread_id in the params
    mock_connector.execute_action.assert_called_once()
    call_args = mock_connector.execute_action.call_args
    params = call_args[0][1]

    assert "thread_id" not in params
    assert params["to"] == "bob@example.com"
    assert result["draft"]["created_in_gmail"] is True
