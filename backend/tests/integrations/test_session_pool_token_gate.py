"""Regression: token-required stdio servers must NOT spawn without a token.

A token-required stdio MCP server (slack/github/notion) spawned via npx with
no token env var fatal-crashes the subprocess and dumps raw Go/Node stack
traces to the console. The pool must refuse to build/enter the Client in that
case and raise a clean, user-actionable error instead.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.client.auth import BearerAuth

from src.integrations.session_pool import UserMCPSessionPool


async def test_slack_stdio_without_token_does_not_spawn_subprocess():
    """slack stdio + no resolved token -> clean error, Client NEVER constructed."""
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "slack",
        {
            "transport": "stdio",
            "auth_provider": "slack",
            "command": "npx",
            "args": ["slack-mcp-server"],
        },
        workspace_id="ws_1",
    )

    client_mock = MagicMock()  # would be the doomed npx spawn

    with (
        # OAuthManager resolves no token for this user -> _resolve_auth returns None
        patch.object(pool, "_resolve_auth", AsyncMock(return_value=None)),
        patch("src.integrations.session_pool.Client", client_mock),
        patch.object(pool, "_register_discovered_tools", AsyncMock()),
    ):
        # McpAuthRequiredError subclasses ConnectionError, so existing
        # `except ConnectionError` boundaries still catch the refusal.
        with pytest.raises((ConnectionError, RuntimeError)) as exc_info:
            await pool.get_or_create_session("slack", user_id="u1", workspace_id="ws_1")

    # The doomed npx subprocess must NEVER be launched.
    client_mock.assert_not_called()
    msg = str(exc_info.value).lower()
    assert "slack" in msg
    assert "re-authorization" in msg


async def test_slack_stdio_with_token_spawns_client():
    """slack stdio + resolved BearerAuth -> Client IS built and entered."""
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "slack",
        {
            "transport": "stdio",
            "auth_provider": "slack",
            "command": "npx",
            "args": ["slack-mcp-server"],
        },
        workspace_id="ws_1",
    )

    fake_client = AsyncMock()
    fake_client.list_tools = AsyncMock(return_value=[])
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)
    client_mock = MagicMock(return_value=fake_ctx)

    with (
        patch.object(
            pool, "_resolve_auth", AsyncMock(return_value=BearerAuth(token="xoxb-real-token"))
        ),
        patch("src.integrations.session_pool.Client", client_mock),
        patch.object(pool, "_register_discovered_tools", AsyncMock()),
    ):
        entry = await pool.get_or_create_session("slack", user_id="u1", workspace_id="ws_1")

    client_mock.assert_called_once()
    fake_ctx.__aenter__.assert_awaited_once()
    assert entry.server_name == "slack"
    assert entry.bound_token == "xoxb-real-token"


async def test_no_auth_stdio_server_still_spawns_without_token():
    """No-auth stdio servers (auth_provider=none) must still spawn without a token.

    The server name here is a stand-in for an admin-registered server, not a seeded one:
    Playwright was the last auth-free seeded server and it is gone, so this test is now
    the only coverage of that spawn branch. Naming it "custom-stdio" would imply the
    catalog still ships one.
    """
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "custom-stdio",
        {"transport": "stdio", "auth_provider": "none", "command": "npx", "args": ["pw-mcp"]},
        workspace_id="ws_1",
    )

    fake_client = AsyncMock()
    fake_client.list_tools = AsyncMock(return_value=[])
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)
    client_mock = MagicMock(return_value=fake_ctx)

    with (
        patch("src.integrations.session_pool.Client", client_mock),
        patch.object(pool, "_register_discovered_tools", AsyncMock()),
    ):
        entry = await pool.get_or_create_session("custom-stdio", user_id="u1", workspace_id="ws_1")

    client_mock.assert_called_once()
    assert entry.server_name == "custom-stdio"
