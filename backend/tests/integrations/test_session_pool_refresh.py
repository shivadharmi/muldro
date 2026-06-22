from unittest.mock import AsyncMock

from src.integrations.session_pool import SessionEntry, UserMCPSessionPool


async def test_refresh_session_closes_auth_free_shared_session():
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "filesystem",
        {"transport": "stdio", "auth_provider": "none", "command": "x"},
        workspace_id="ws",
    )
    ctx = AsyncMock()
    ctx.__aexit__ = AsyncMock(return_value=None)
    key = ("ws", "filesystem", "__shared__")
    pool._sessions[key] = SessionEntry(
        client=AsyncMock(),
        client_ctx=ctx,
        server_name="filesystem",
        user_id="__shared__",
        tools={},
    )
    # Caller passes the REAL user id, not the sentinel — must still close it.
    await pool.refresh_session("filesystem", "real_user", workspace_id="ws")
    ctx.__aexit__.assert_awaited_once()
    assert key not in pool._sessions
