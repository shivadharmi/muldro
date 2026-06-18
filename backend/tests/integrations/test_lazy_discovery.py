from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.lazy_discovery import discover_missing_schemas


async def test_discovers_servers_with_no_persisted_schema():
    class _Tool:
        def __init__(self, name, server, schema):
            self.name = name
            self.server = server
            self.input_schema = schema

    tools = [_Tool("create_pr", "github", None), _Tool("list_prs", "github", None)]
    pool = AsyncMock()
    pool.is_discovered = MagicMock(return_value=False)
    with patch("src.integrations.lazy_discovery.get_workspace_pool", return_value=pool):
        servers = await discover_missing_schemas(tools, workspace_id="ws_1")
    assert servers == {"github"}
    pool.discover_and_persist.assert_awaited_once_with("github", workspace_id="ws_1")


async def test_skips_servers_with_persisted_schema():
    class _Tool:
        def __init__(self, name, server, schema):
            self.name = name
            self.server = server
            self.input_schema = schema

    tools = [_Tool("create_pr", "github", {"type": "object"})]
    pool = AsyncMock()
    pool.is_discovered = MagicMock(return_value=False)
    with patch("src.integrations.lazy_discovery.get_workspace_pool", return_value=pool):
        servers = await discover_missing_schemas(tools, workspace_id="ws_1")
    assert servers == set()
    pool.discover_and_persist.assert_not_awaited()


async def test_discover_missing_schemas_skips_already_discovered_servers():
    class _Tool:
        def __init__(self, name, server, schema):
            self.name = name
            self.server = server
            self.input_schema = schema

    tools = [_Tool("create_pr", "github", None)]
    pool = AsyncMock()
    pool.is_discovered = MagicMock(return_value=True)
    with patch("src.integrations.lazy_discovery.get_workspace_pool", return_value=pool):
        servers = await discover_missing_schemas(tools, workspace_id="ws_1")
    assert servers == set()
    pool.discover_and_persist.assert_not_awaited()
