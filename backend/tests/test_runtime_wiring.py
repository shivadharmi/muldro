"""Regression tests for startup wiring.

1. The worker's MCP-bridge handshake never completed because ``run.py`` (run as
   ``__main__``) and the API lifespan (``from run import ...``) bound *different*
   Event objects. Both must now share one object via ``src.runtime_signals``.
2. An internal MCP server whose module-level ``configure()`` is never called comes up
   with its runtime dependencies unset and fails silently at call time rather than at
   boot. ``configure_tool_servers`` is the single entry point that must reach every one
   of them.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.tools import configure_tool_servers
from src.tools.intelligence_server import _shared as intel_shared


def test_mcp_bridge_ready_is_a_single_shared_event():
    """run.py and the API lifespan must reference the SAME Event object."""
    import run
    from src import runtime_signals

    assert run.mcp_bridge_ready is runtime_signals.mcp_bridge_ready


def test_configure_tool_servers_configures_the_intelligence_server():
    """The shared db_factory reaches the intelligence server through the one entry point."""
    services = SimpleNamespace(extras={"redis": object()})
    db_factory = MagicMock()

    # Reset to prove configure_tool_servers is what sets it.
    intel_shared._db_factory = None

    configure_tool_servers(db_factory, MagicMock(), services)

    assert intel_shared._db_factory is db_factory


def test_configure_tool_servers_tolerates_missing_extras():
    """An empty extras dict must not crash the wiring."""
    services = SimpleNamespace(extras={})
    db_factory = MagicMock()

    configure_tool_servers(db_factory, MagicMock(), services)

    assert intel_shared._db_factory is db_factory
