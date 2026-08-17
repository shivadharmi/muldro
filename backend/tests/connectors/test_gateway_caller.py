"""GatewayToolCaller is the only seam between a connector and MCP transport."""

from unittest.mock import AsyncMock, patch

import pytest

from src.connectors.gateway_caller import GatewayToolCaller


async def test_call_maps_action_id_to_underscore_tool_name():
    """Connectors hold dotted actionIds; the LLM-legal tool name is derived here."""
    with patch("src.connectors.gateway_caller.call_mcp_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"status": "ok", "result": {"emailAddress": "a@b.c"}}
        caller = GatewayToolCaller(user_id="usr_1", workspace_id="ws_1")
        result = await caller.call("gmail.get_profile", {"userId": "me"})

    assert result == {"status": "ok", "result": {"emailAddress": "a@b.c"}}
    mock_call.assert_awaited_once_with(
        "gmail_get_profile",
        {"userId": "me"},
        user_id="usr_1",
        workspace_id="ws_1",
    )


async def test_call_forwards_bound_identity_not_arguments():
    """user_id/workspace_id come from the caller's binding, never from the payload."""
    with patch("src.connectors.gateway_caller.call_mcp_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"status": "ok"}
        caller = GatewayToolCaller(user_id="usr_owner", workspace_id="ws_owner")
        await caller.call("gmail.get_profile", {"user_id": "usr_attacker"})

    kwargs = mock_call.await_args.kwargs
    assert kwargs["user_id"] == "usr_owner"
    assert kwargs["workspace_id"] == "ws_owner"


async def test_illegal_action_id_raises_rather_than_calling():
    """An un-nameable actionId must fail loudly, not reach the transport."""
    with patch("src.connectors.gateway_caller.call_mcp_tool", new_callable=AsyncMock) as mock_call:
        caller = GatewayToolCaller(user_id="usr_1", workspace_id="ws_1")
        with pytest.raises(ValueError):
            await caller.call("gmail.this has spaces", {})
    mock_call.assert_not_awaited()


def test_caller_is_frozen():
    """Identity must not be mutable after construction."""
    caller = GatewayToolCaller(user_id="usr_1", workspace_id="ws_1")
    with pytest.raises(Exception):
        caller.user_id = "usr_2"  # type: ignore[misc]
