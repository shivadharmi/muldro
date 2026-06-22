from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.mcp_pool import WorkspaceMCPPool


async def test_discover_and_persist_records_failure_on_error():
    session_pool = MagicMock()
    session_pool.has_server_config = MagicMock(return_value=True)
    session_pool.get_or_create_session = AsyncMock(side_effect=RuntimeError("boom"))
    session_pool.refresh_session = AsyncMock()
    pool = WorkspaceMCPPool(session_pool=session_pool)
    pool._resolve_workspace_user = AsyncMock(return_value="u1")

    recorded = []
    with patch(
        "src.connectors.mcp_bridge.record_discovery_failure",
        side_effect=lambda name, err: recorded.append((name, err)),
    ):
        count = await pool.discover_and_persist("github", workspace_id="ws")

    assert count == 0
    assert recorded and recorded[0][0] == "github"


async def test_discover_and_persist_clears_failure_on_success():
    class _FakeSession:
        tools = {"create_pr": "github"}

    session_pool = MagicMock()
    session_pool.has_server_config = MagicMock(return_value=True)
    session_pool.get_or_create_session = AsyncMock(return_value=_FakeSession())
    session_pool.refresh_session = AsyncMock()
    pool = WorkspaceMCPPool(session_pool=session_pool)
    pool._resolve_workspace_user = AsyncMock(return_value="u1")

    cleared = []
    with patch(
        "src.connectors.mcp_bridge.clear_discovery_failure",
        side_effect=lambda name: cleared.append(name),
    ):
        count = await pool.discover_and_persist("github", workspace_id="ws")

    assert count == 1
    assert cleared == ["github"]
