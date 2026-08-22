"""Tests for ToolExecutor.execute_tool workspace_id injection scoping."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


def _make_tool_record(backend: str, server: str = "default"):
    """Create a mock tool registry record."""
    tool = MagicMock()
    tool.backend = backend
    tool.server = server
    tool.enabled = True
    return tool


def _make_tool_executor(mock_db):
    """Build a ToolExecutor with a mocked event publisher and db factory."""
    from src.orchestrator.tool_executor import ToolExecutor

    events = MagicMock()
    events.publish_event = AsyncMock()

    db_factory = MagicMock()
    db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    return ToolExecutor(events, lambda: db_factory)


class TestWorkspaceIdInjection:
    """workspace_id must NOT be injected into external_mcp tool inputs."""

    @pytest.mark.asyncio
    async def test_external_mcp_no_workspace_id_in_input(self):
        """External MCP tool calls must not have workspace_id in tool_input."""
        mock_call_mcp = AsyncMock(return_value={"status": "ok", "result": "done"})

        tool = _make_tool_record("external_mcp")

        mock_db = AsyncMock()
        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        te = _make_tool_executor(mock_db)

        with (
            patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry),
            patch("src.connectors.mcp_bridge.call_mcp_tool", mock_call_mcp),
        ):
            await te.execute_tool(
                tool_name="search_gmail_messages",
                tool_input={"query": "test"},
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            # Verify call_mcp_tool was called
            mock_call_mcp.assert_called_once()

            # Get the tool_input argument (second positional arg)
            call_args = mock_call_mcp.call_args
            if len(call_args[0]) > 1:
                tool_input_sent = call_args[0][1]
            else:
                tool_input_sent = call_args[1].get("arguments", {})
            assert "workspace_id" not in tool_input_sent, (
                f"External MCP tool received workspace_id in input: {tool_input_sent}"
            )

    @pytest.mark.asyncio
    async def test_internal_mcp_gets_workspace_id(self):
        """Internal MCP tool calls must have workspace_id in tool_input."""
        tool = _make_tool_record("internal_mcp", server="intelligence")

        mock_db = AsyncMock()
        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        te = _make_tool_executor(mock_db)
        te.call_internal_tool = AsyncMock(return_value={"status": "ok"})

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            await te.execute_tool(
                tool_name="search",
                tool_input={"query": "test"},
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            # Verify call_internal_tool was called
            te.call_internal_tool.assert_called_once()

            # Get the tool_input argument (second positional arg)
            call_args = te.call_internal_tool.call_args
            if len(call_args[0]) > 1:
                tool_input_sent = call_args[0][1]
            else:
                tool_input_sent = call_args[1]
            assert "workspace_id" in tool_input_sent, (
                f"Internal MCP tool must receive workspace_id: {tool_input_sent}"
            )

    @pytest.mark.asyncio
    async def test_intelligence_tool_gets_user_id_and_workspace_id(self):
        """Intelligence tools (impl declares both) get user_id AND workspace_id."""
        tool = _make_tool_record("internal_mcp", server="intelligence")

        mock_db = AsyncMock()
        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        te = _make_tool_executor(mock_db)
        te.call_internal_tool = AsyncMock(return_value={"status": "ok"})

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            await te.execute_tool(
                tool_name="search",
                tool_input={"query": "test"},
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            te.call_internal_tool.assert_called_once()
            sent = _sent_input(te.call_internal_tool.call_args)
            assert sent.get("user_id") == TEST_USER_ID, (
                f"Intelligence tool must receive user_id: {sent}"
            )
            assert sent.get("workspace_id") == TEST_WORKSPACE_ID, (
                f"Intelligence tool must receive workspace_id: {sent}"
            )


def _sent_input(call_args):
    """Extract the tool_input passed to call_internal_tool (2nd positional or kwarg)."""
    if len(call_args[0]) > 1:
        return call_args[0][1]
    return call_args[1].get("tool_input", call_args[1])


class TestContextualArgInjection:
    """Signature-aware contextual-arg injection for internal MCP tools.

    Injection is driven by each tool impl's declared parameters, not the server
    name: a tool receives a contextual arg only if its implementation declares it.
    """

    @pytest.mark.asyncio
    async def test_a_tool_declaring_both_gets_both_injected(self):
        """Regression: a missing injected user_id used to surface as a tool error."""
        tool = _make_tool_record("internal_mcp", server="intelligence")

        mock_db = AsyncMock()
        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        te = _make_tool_executor(mock_db)
        te.call_internal_tool = AsyncMock(return_value={"results": []})

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            await te.execute_tool(
                tool_name="search",
                tool_input={"query": "muldro"},
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            te.call_internal_tool.assert_called_once()
            sent = _sent_input(te.call_internal_tool.call_args)
            assert sent.get("user_id") == TEST_USER_ID, f"search must receive user_id: {sent}"
            assert sent.get("workspace_id") == TEST_WORKSPACE_ID, (
                f"search must receive workspace_id: {sent}"
            )

    @pytest.mark.asyncio
    async def test_an_arg_the_impl_does_not_declare_is_not_injected(self):
        """The negative half: injecting an undeclared arg is a TypeError at the impl.

        Driven through a substituted signature map rather than whichever shipped tool
        happens to declare one arg today, because the property belongs to the injector
        and must hold no matter what the catalog looks like.
        """
        tool = _make_tool_record("internal_mcp", server="intelligence")

        mock_db = AsyncMock()
        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        te = _make_tool_executor(mock_db)
        te.call_internal_tool = AsyncMock(return_value={"status": "ok"})

        with (
            patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry),
            patch(
                "src.orchestrator.tool_executor._internal_tool_context_args",
                return_value={"user_id_only_tool": frozenset({"user_id"})},
            ),
        ):
            await te.execute_tool(
                tool_name="user_id_only_tool",
                tool_input={"thing": "x"},
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            sent = _sent_input(te.call_internal_tool.call_args)
            assert sent.get("user_id") == TEST_USER_ID
            assert "workspace_id" not in sent, (
                f"the impl declares no workspace_id — it must not be injected: {sent}"
            )

    def test_context_arg_map_reflects_impl_signatures(self):
        """The introspected map matches the actual impl signatures.

        ``active_plans_resource`` declares only workspace_id, so this also proves the
        map is really introspected rather than returning both args for everything.
        """
        from src.orchestrator.tool_executor import _internal_tool_context_args

        mapping = _internal_tool_context_args()
        assert mapping["active_plans_resource"] == frozenset({"workspace_id"})
        assert mapping["search"] == frozenset({"user_id", "workspace_id"})
