"""Tests for ToolRegistry pre-dispatch checks + hooks registry lookup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.hooks import governor_pre_tool_hook
from src.orchestrator.tool_policy import FALLBACK_BLOCKED_TOOLS, FALLBACK_WRITE_TOOLS
from tests.conftest import TEST_USER_ID

# ── Hooks: governor_pre_tool_hook ────────────────────────────────────────────


class TestGovernorPreToolHook:
    """Tests for governor_pre_tool_hook with ToolRegistry integration."""

    @pytest.mark.parametrize("tool", ["search_memory", "gmail_list", "calendar_get"])
    async def test_read_only_tools_always_allowed(self, tool):
        result = await governor_pre_tool_hook(tool, {}, "operator", user_id=TEST_USER_ID)
        assert result == {"allowed": True}

    @pytest.mark.parametrize("tool", ["gmail_delete", "drive_delete"])
    async def test_blocked_tools_never_allowed(self, tool):
        result = await governor_pre_tool_hook(tool, {}, "operator", user_id=TEST_USER_ID)
        assert result["allowed"] is False
        assert "blocked" in result["reason"].lower()

    async def test_write_tool_requires_approval_no_db(self):
        result = await governor_pre_tool_hook("gmail_send", {}, "operator", user_id=TEST_USER_ID)
        assert result["allowed"] is False
        assert result.get("approval_required") is True

    async def test_internal_tool_allowed(self):
        result = await governor_pre_tool_hook("ingest_event", {}, "observer", user_id=TEST_USER_ID)
        assert result == {"allowed": True}

    async def test_registry_classify_via_db(self):
        """ToolRegistry classify returns tool metadata from DB."""
        mock_db = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.enabled = True
        mock_tool.requires_approval = True
        mock_tool.risk_level = "high"

        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=mock_tool)

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_db)
        mock_context.__aexit__ = AsyncMock(return_value=False)

        def db_factory():
            return mock_context

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor_pre_tool_hook(
                "custom_write_tool", {}, "operator", user_id=TEST_USER_ID, db_factory=db_factory
            )

        assert result["allowed"] is False
        assert result.get("approval_required") is True

    async def test_registry_unavailable_falls_back_to_hardcoded(self):
        """If ToolRegistry raises, falls back to hardcoded WRITE_TOOLS."""

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        mock_context.__aexit__ = AsyncMock(return_value=False)

        def db_factory():
            return mock_context

        # gmail_send is in hardcoded WRITE_TOOLS, so still caught
        result = await governor_pre_tool_hook(
            "gmail_send", {}, "operator", user_id=TEST_USER_ID, db_factory=db_factory
        )
        assert result["allowed"] is False
        assert result.get("approval_required") is True

    async def test_unknown_tool_not_in_any_set_allowed(self):
        """Tools not in BLOCKED or WRITE fallback sets pass through."""
        result = await governor_pre_tool_hook(
            "my_custom_internal_tool", {}, "operator", user_id=TEST_USER_ID
        )
        assert result == {"allowed": True}

    async def test_write_tool_with_db_creates_approval(self):
        """Write tool with db_factory + services creates Approval record."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_db)
        mock_context.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def db_factory():
            nonlocal call_count
            call_count += 1
            return mock_context

        services = {"some_service": True}

        # First call: _classify_via_registry returns not blocked, is write
        mock_tool = MagicMock()
        mock_tool.enabled = True
        mock_tool.requires_approval = True
        mock_tool.risk_level = "high"

        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=mock_tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor_pre_tool_hook(
                "gmail_send",
                {"to": "a@b.com", "subject": "Hi"},
                "operator",
                user_id=TEST_USER_ID,
                db_factory=db_factory,
                services=services,
            )

        assert result["allowed"] is False
        assert result.get("approval_required") is True
        assert "approval_id" in result


class TestHookConstants:
    """Verify hook constant sets are coherent."""

    def test_no_overlap_write_and_blocked(self):
        overlap = FALLBACK_WRITE_TOOLS & FALLBACK_BLOCKED_TOOLS
        assert not overlap, f"Overlap: {overlap}"

    def test_write_tools_not_empty(self):
        assert len(FALLBACK_WRITE_TOOLS) > 0

    def test_blocked_tools_not_empty(self):
        assert len(FALLBACK_BLOCKED_TOOLS) > 0


# ── Orchestrator _execute_tool pre-dispatch ──────────────────────────────────


class TestOrchestratorToolDispatch:
    """Tests for ToolRegistry pre-dispatch in JarvisOrchestrator._execute_tool."""

    def _make_orchestrator(self):
        """Create minimal orchestrator mock for _execute_tool testing."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        settings = MagicMock()
        settings.anthropic_model = "claude-sonnet-4-20250514"
        settings.use_bedrock = False
        settings.anthropic_api_key = "test-key"
        settings.daily_token_budget_usd = 5.0
        settings.bedrock_region = "us-east-1"

        with patch("src.orchestrator.jarvis.get_anthropic_client"):
            orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
            orch._settings = settings
            orch._db_factory = MagicMock()
            orch._event_bus = None
            orch._agents = {}
            orch._services = MagicMock()
        return orch

    async def test_blocked_tool_returns_error(self):
        """ToolRegistry blocked tool returns error dict without executing."""
        orch = self._make_orchestrator()

        mock_db = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_db)
        mock_context.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=mock_context)

        mock_tool = MagicMock()
        mock_tool.enabled = False
        mock_tool.requires_approval = False
        mock_tool.risk_level = "critical"

        mock_registry = AsyncMock()
        mock_registry.get_tool = AsyncMock(return_value=mock_tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await orch._execute_tool("disabled_tool", {"arg": "val"}, user_id=TEST_USER_ID)

        assert result["blocked"] is True
        assert "disabled" in result["error"].lower() or "blocked" in result["error"].lower()

    async def test_non_blocked_tool_proceeds_to_internal(self):
        """Non-blocked tool falls through to internal handler lookup."""
        orch = self._make_orchestrator()

        mock_db = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_db)
        mock_context.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=mock_context)

        mock_registry = AsyncMock()
        mock_registry.is_blocked_tool = AsyncMock(return_value=False)

        mock_handler = AsyncMock(return_value={"status": "ok"})

        with (
            patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry),
            patch("src.tools.intelligence_server.search_memory", mock_handler),
        ):
            result = await orch._execute_tool(
                "search_memory", {"query": "test"}, user_id=TEST_USER_ID
            )

        assert result == {"status": "ok"}

    async def test_registry_error_skips_precheck(self):
        """If ToolRegistry raises, pre-check is skipped and tool proceeds."""
        orch = self._make_orchestrator()

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_context.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=mock_context)

        mock_handler = AsyncMock(return_value={"data": "result"})

        with patch("src.tools.intelligence_server.ingest_event", mock_handler):
            result = await orch._execute_tool(
                "ingest_event", {"event": "test"}, user_id=TEST_USER_ID
            )

        assert result == {"data": "result"}

    async def test_mcp_tool_dispatched_when_not_internal(self):
        """Tools not in internal_handlers check MCP bridge."""
        orch = self._make_orchestrator()

        mock_db = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_db)
        mock_context.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=mock_context)

        mock_registry = AsyncMock()
        mock_registry.is_blocked_tool = AsyncMock(return_value=False)

        with (
            patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry),
            patch("src.connectors.mcp_bridge.is_mcp_tool", return_value=True),
            patch(
                "src.connectors.mcp_bridge.call_mcp_tool",
                AsyncMock(return_value={"mcp": True}),
            ) as mock_call,
        ):
            result = await orch._execute_tool(
                "gmail_read_email", {"id": "123"}, user_id=TEST_USER_ID
            )

        assert result == {"mcp": True}
        mock_call.assert_called_once_with("gmail_read_email", {"id": "123"})

    async def test_connector_fallback_when_not_mcp(self):
        """Falls to connector-backed execution when not internal or MCP."""
        orch = self._make_orchestrator()

        mock_db = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_db)
        mock_context.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=mock_context)

        mock_registry = AsyncMock()
        mock_registry.is_blocked_tool = AsyncMock(return_value=False)

        orch._execute_connector_tool = AsyncMock(return_value={"connector": True})

        with (
            patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry),
            patch("src.connectors.mcp_bridge.is_mcp_tool", return_value=False),
        ):
            result = await orch._execute_tool("some_connector_tool", {"x": 1}, user_id=TEST_USER_ID)

        assert result == {"connector": True}
        orch._execute_connector_tool.assert_called_once()
