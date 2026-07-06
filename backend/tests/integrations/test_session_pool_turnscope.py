from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.session_pool import UserMCPSessionPool
from src.integrations.turn_scope import turn_scope


async def test_session_create_and_reuse_tracked_in_turn_scope():
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "example_stdio",
        {"transport": "stdio", "auth_provider": "none", "command": "x"},
        workspace_id="ws",
    )

    fake_client = AsyncMock()
    fake_client.list_tools = AsyncMock(return_value=[])
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    captured = []
    with (
        patch("src.integrations.session_pool.Client", MagicMock(return_value=fake_ctx)),
        patch.object(pool, "_register_discovered_tools", AsyncMock()),
    ):
        async with turn_scope(on_close=lambda keys: captured.append(keys)):
            await pool.get_or_create_session("example_stdio", user_id="u", workspace_id="ws")
            await pool.get_or_create_session("example_stdio", user_id="u", workspace_id="ws")
    key = ("ws", "example_stdio", "__shared__")
    assert captured == [[key]]
