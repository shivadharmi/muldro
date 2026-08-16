"""Unit tests for the OpenConnector MCP client wrapper.

`call_openconnector` is a thin single-call-point wrapper around the shared
OpenConnector MCP endpoint. The MCP round trip itself lives in
`_client_call`, which is patched here so the test exercises only the public
seam — no real network/MCP session is created.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.adapter.openconnector_client import call_openconnector, get_action_guide


@pytest.mark.asyncio
async def test_call_openconnector_returns_client_call_result_and_forwards_tool_name():
    mock_result = {"content": [{"type": "text", "text": "ok"}]}

    with patch(
        "src.adapter.openconnector_client._client_call",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_client_call:
        result = await call_openconnector("execute_action", {"actionId": "gmail.fetch_emails"})

    assert result == mock_result
    mock_client_call.assert_awaited_once()
    args, _ = mock_client_call.call_args
    assert args[0] == "execute_action"


@pytest.mark.asyncio
async def test_get_action_guide_calls_the_runtime_tool_with_action_id():
    with patch(
        "src.adapter.openconnector_client._client_call",
        new_callable=AsyncMock,
        return_value={"inputSchema": {"type": "object"}},
    ) as mock_call:
        result = await get_action_guide("gmail.fetch_emails")

    mock_call.assert_awaited_once_with("get_action_guide", {"actionId": "gmail.fetch_emails"})
    assert result == {"inputSchema": {"type": "object"}}
