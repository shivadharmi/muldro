from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.session_pool import UserMCPSessionPool


async def test_managed_local_server_resolves_url_and_releases_on_teardown():
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "google-workspace",
        {"transport": "streamable-http", "auth_provider": "none", "managed_local": True},
        workspace_id="ws_1",
    )

    mgr = AsyncMock()
    mgr.ensure_running = AsyncMock(return_value="http://127.0.0.1:5/mcp")
    mgr.release = AsyncMock()

    fake_client = AsyncMock()
    fake_client.list_tools = AsyncMock(return_value=[])
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("src.integrations.session_pool.get_local_process_manager", return_value=mgr),
        patch("src.integrations.session_pool.Client", MagicMock(return_value=fake_ctx)),
        patch.object(pool, "_register_discovered_tools", AsyncMock()),
    ):
        entry = await pool.get_or_create_session(
            "google-workspace", user_id="u1", workspace_id="ws_1"
        )
        assert entry.managed_server == "google-workspace"
        mgr.ensure_running.assert_awaited_once_with("google-workspace")

        # auth_provider == "none" -> session keyed under __shared__; teardown
        # with the real user must still release the managed process.
        await pool.refresh_session("google-workspace", "u1", workspace_id="ws_1")
        mgr.release.assert_awaited_once_with("google-workspace")


async def test_release_managed_noop_when_not_managed():
    from src.integrations.session_pool import SessionEntry

    pool = UserMCPSessionPool()
    entry = SessionEntry(
        client=AsyncMock(),
        client_ctx=AsyncMock(),
        server_name="filesystem",
        user_id="u",
        tools={},
        managed_server=None,
    )
    mgr = AsyncMock()
    mgr.release = AsyncMock()
    with patch("src.integrations.session_pool.get_local_process_manager", return_value=mgr):
        await pool._release_managed(entry)
    mgr.release.assert_not_awaited()
