"""Unit tests for the OpenConnector MCP client wrapper.

`call_openconnector` is a thin single-call-point wrapper around the shared
OpenConnector MCP endpoint. The MCP round trip itself lives in
`_client_call`, which is patched here so the test exercises only the public
seam — no real network/MCP session is created.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.adapter.openconnector_client import call_openconnector


@pytest.mark.asyncio
async def test_call_openconnector_returns_client_call_result_and_forwards_tool_name():
    mock_result = {"content": [{"type": "text", "text": "ok"}]}

    with patch(
        "src.adapter.openconnector_client._client_call",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_client_call:
        result = await call_openconnector("execute_action", {"actionId": "gmail.search"})

    assert result == mock_result
    mock_client_call.assert_awaited_once()
    args, _ = mock_client_call.call_args
    assert args[0] == "execute_action"
