"""Tests for _execute_tool workspace_id injection scoping."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_tool_record(backend: str, server: str = "default"):
    """Create a mock tool registry record."""
    tool = MagicMock()
    tool.backend = backend
    tool.server = server
    tool.enabled = True
    return tool


class TestWorkspaceIdInjection:
    """workspace_id must NOT be injected into external_mcp tool inputs."""

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_external_mcp_no_workspace_id_in_input(self, mock_client):
        """External MCP tool calls must not have workspace_id in tool_input."""
        mock_call_mcp = AsyncMock(return_value={"status": "ok", "result": "done"})

        orchestrator = MagicMock()
        orchestrator._db_factory = MagicMock()
        orchestrator._settings = make_mock_settings()
        orchestrator._publish_event = AsyncMock()

        tool = _make_tool_record("external_mcp")

        mock_db = AsyncMock()
        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        # Mock the async context manager for db_factory
        orchestrator._db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        orchestrator._db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry),
            patch("src.connectors.mcp_bridge.call_mcp_tool", mock_call_mcp),
        ):
            from src.orchestrator.jarvis import JarvisOrchestrator

            await JarvisOrchestrator._execute_tool(
                orchestrator,
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
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_internal_mcp_gets_workspace_id(self, mock_client):
        """Internal MCP tool calls must have workspace_id in tool_input."""
        orchestrator = MagicMock()
        orchestrator._db_factory = MagicMock()
        orchestrator._settings = make_mock_settings()
        orchestrator._publish_event = AsyncMock()
        orchestrator._call_internal_tool = AsyncMock(return_value={"status": "ok"})

        tool = _make_tool_record("internal_mcp", server="intelligence")

        mock_db = AsyncMock()
        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        orchestrator._db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        orchestrator._db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            from src.orchestrator.jarvis import JarvisOrchestrator

            await JarvisOrchestrator._execute_tool(
                orchestrator,
                tool_name="search",
                tool_input={"query": "test"},
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            # Verify _call_internal_tool was called
            orchestrator._call_internal_tool.assert_called_once()

            # Get the tool_input argument (second positional arg)
            call_args = orchestrator._call_internal_tool.call_args
            if len(call_args[0]) > 1:
                tool_input_sent = call_args[0][1]
            else:
                tool_input_sent = call_args[1]
            assert "workspace_id" in tool_input_sent, (
                f"Internal MCP tool must receive workspace_id: {tool_input_sent}"
            )
