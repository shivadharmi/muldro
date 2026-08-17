"""The schema shown to the agent must belong to the server the call dispatches to.

A tool's identity is (workspace, server, name), so ``list_mcp_tools`` may
legitimately return two rows sharing one name. ``get_tools_for_agent`` used to
re-collapse them into a bare-name dict, last-write-wins by discovery order,
while ``get_server_for_tool`` dispatches to the lexicographically-first server —
so the agent could be handed server Z's parameter schema for a call that routes
to server A, producing a param mismatch at the adapter blamed on the model.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.integrations.session_pool import UserMCPSessionPool
from src.orchestrator.agents import SubAgent
from src.orchestrator.tool_executor import ToolExecutor

_ALPHA_SCHEMA = {
    "type": "object",
    "properties": {"alpha_param": {"type": "string"}},
    "required": ["alpha_param"],
}
_ZETA_SCHEMA = {
    "type": "object",
    "properties": {"zeta_param": {"type": "string"}},
    "required": ["zeta_param"],
}


class _FakeDBFactory:
    """Stand-in for ``async with self._db_factory() as db:`` — no real DB."""

    def __call__(self):
        return self

    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *exc):
        return False


def _fake_registry_cls(db_tools):
    class _FakeRegistry:
        def __init__(self, db, workspace_id=None):
            pass

        async def get_tool(self, name):
            # No internal tool is in scope — keeps the built list to externals.
            return None

        async def list_tools(self, enabled_only=True, workspace_scoped=True):
            return db_tools

    return _FakeRegistry


async def test_agent_is_shown_the_schema_of_the_server_the_call_dispatches_to():
    # The DB holds one row for the shared name, owned by 'alpha' — that is what
    # _register_discovered_tools produces, since it looks the name up bare and
    # enriches the existing row rather than adding a second one.
    db_tool = SimpleNamespace(
        name="shared_tool",
        server="alpha",
        capability="email.read",
        input_schema=None,
        description="db description",
    )

    # Discovery order puts zeta LAST, so a bare-name dict keeps zeta's schema.
    live_rows = [
        {"name": "shared_tool", "server": "alpha", "input_schema": _ALPHA_SCHEMA},
        {"name": "shared_tool", "server": "zeta", "input_schema": _ZETA_SCHEMA},
    ]

    # The dispatch side of the same collision, from the real resolver.
    pool = UserMCPSessionPool()
    pool._server_tools[("ws_1", "zeta")] = {"shared_tool": "shared_tool"}
    pool._server_tools[("ws_1", "alpha")] = {"shared_tool": "shared_tool"}
    dispatch_server = pool.get_server_for_tool("shared_tool", workspace_id="ws_1")
    assert dispatch_server == "alpha"

    executor = ToolExecutor(events=MagicMock(), db_factory_provider=_FakeDBFactory())
    agent = SubAgent(
        name="perceiver",
        prompt="",
        model_tier="sonnet",
        capability_scope={"email.read"},
    )

    with (
        patch("src.connectors.mcp_bridge.list_mcp_tools", return_value=live_rows),
        patch("src.services.tool_registry.ToolRegistry", _fake_registry_cls([db_tool])),
    ):
        tools = await executor.get_tools_for_agent(agent, workspace_id="ws_1")

    offered = [t for t in tools if t["name"] == "shared_tool"]
    assert len(offered) == 1, f"expected one shared_tool entry, got {offered}"
    assert offered[0]["input_schema"] == _ALPHA_SCHEMA, (
        f"agent was shown {dispatch_server!r}'s sibling's schema; the call dispatches "
        f"to {dispatch_server!r}, so this is a silent param mismatch: {offered[0]}"
    )
