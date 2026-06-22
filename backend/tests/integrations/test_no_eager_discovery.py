import inspect

import src.connectors.mcp_bridge as bridge
import src.integrations.mcp_pool as pool_mod


def test_initialize_from_db_does_not_discover():
    src = inspect.getsource(pool_mod.WorkspaceMCPPool.initialize_from_db)
    assert "discover_tools" not in src
    assert "_discover_stdio_schemas" not in src


def test_bridge_init_has_no_discovery_task():
    src = inspect.getsource(bridge.initialize_mcp_bridge)
    assert "_discover(" not in src
    assert "create_task" not in src
