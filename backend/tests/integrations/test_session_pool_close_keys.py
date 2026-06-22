from unittest.mock import AsyncMock

from src.integrations.session_pool import SessionEntry, UserMCPSessionPool


async def test_close_keys_exits_context_and_releases_managed():
    pool = UserMCPSessionPool()
    ctx = AsyncMock()
    ctx.__aexit__ = AsyncMock(return_value=None)
    key = ("ws", "google-workspace", "u")
    pool._sessions[key] = SessionEntry(
        client=AsyncMock(),
        client_ctx=ctx,
        server_name="google-workspace",
        user_id="u",
        tools={},
        managed_server="google-workspace",
    )
    released = []
    pool._release_managed = AsyncMock(side_effect=lambda e: released.append(e.managed_server))

    await pool.close_keys([key])
    ctx.__aexit__.assert_awaited_once()
    assert released == ["google-workspace"]
    assert key not in pool._sessions
