"""Regression tests for two startup-wiring bugs surfaced in production logs:

1. The worker's MCP-bridge handshake never completed because ``run.py`` (run as
   ``__main__``) and the API lifespan (``from run import ...``) bound *different*
   Event objects. Both must now share one object via ``src.runtime_signals``.
2. ``push_ui_update`` always returned ``redis_not_available`` because
   ``communication_server.configure()`` was never called. ``configure_tool_servers``
   must wire the communication server's Redis from the shared container.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.tools import communication_server, configure_tool_servers
from src.tools.intelligence_server import _shared as intel_shared


def test_mcp_bridge_ready_is_a_single_shared_event():
    """run.py and the API lifespan must reference the SAME Event object."""
    import run
    from src import runtime_signals

    assert run.mcp_bridge_ready is runtime_signals.mcp_bridge_ready


def test_configure_tool_servers_wires_communication_redis():
    """The communication server's Redis is taken from services.extras['redis']."""
    sentinel_redis = object()
    services = SimpleNamespace(extras={"redis": sentinel_redis})
    db_factory = MagicMock()
    settings = MagicMock()

    # Reset to prove configure_tool_servers is what sets it.
    communication_server._redis = None

    configure_tool_servers(db_factory, settings, services)

    assert communication_server._redis is sentinel_redis
    # Intelligence server was configured in the same call.
    assert intel_shared._db_factory is db_factory


def test_configure_tool_servers_tolerates_missing_redis():
    """No Redis in extras → communication server configured with redis=None (no crash)."""
    services = SimpleNamespace(extras={})
    communication_server._redis = object()  # stale value to overwrite

    configure_tool_servers(MagicMock(), MagicMock(), services)

    assert communication_server._redis is None
