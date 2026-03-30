"""Tests for Phase 11: Feature Flag + Registry-Driven Dispatch.

Covers: unified dispatch, can_use_tool_unified, is_auto_execute_tool,
flag gating, and composite/internal/external backend routing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


def _make_tool_record(
    name="search",
    backend="internal_mcp",
    server="intelligence",
    enabled=True,
    capability="internal.search",
    risk_level="low",
    requires_approval=False,
):
    """Create a mock ToolDefinition record."""
    tool = MagicMock()
    tool.name = name
    tool.backend = backend
    tool.server = server
    tool.enabled = enabled
    tool.capability = capability
    tool.risk_level = risk_level
    tool.requires_approval = requires_approval
    return tool


class TestFeatureFlag:
    def test_flag_defaults_to_false(self):
        """JARVIS_USE_UNIFIED_DISPATCH defaults to False."""
        settings = make_mock_settings()
        assert settings.use_unified_dispatch is False

    def test_flag_can_be_enabled(self):
        """Flag can be set to True via make_mock_settings override."""
        settings = make_mock_settings(use_unified_dispatch=True)
        assert settings.use_unified_dispatch is True


class TestCallCompositeTool:
    """Tests for _call_composite_tool() extracted handler."""

    @pytest.fixture
    def orchestrator(self):
        """Create a minimal JarvisOrchestrator with mocked dependencies."""
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.orchestrator.services import ServiceContainer

        settings = make_mock_settings(use_unified_dispatch=True)
        db_factory = MagicMock()
        return JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(),
        )

    @pytest.mark.asyncio
    async def test_composite_web_search(self, orchestrator):
        """web_search dispatches to the web_search module."""
        with patch("src.browser.web_search.web_search", new_callable=AsyncMock) as mock_ws:
            mock_ws.return_value = {"results": [{"title": "test", "url": "http://example.com"}]}
            result = await orchestrator._call_composite_tool(
                "web_search", {"query": "test"}, user_id="usr_1", workspace_id="ws_1"
            )
        mock_ws.assert_called_once_with(
            query="test", num_results=10, user_id="usr_1", workspace_id="ws_1"
        )
        assert "results" in result

    @pytest.mark.asyncio
    async def test_composite_unknown_tool(self, orchestrator):
        """Unknown composite tool returns error."""
        result = await orchestrator._call_composite_tool(
            "unknown_composite", {}, user_id="usr_1", workspace_id="ws_1"
        )
        assert "error" in result
        assert "Unknown composite tool" in result["error"]


class TestCallInternalToolServerPrefix:
    """Tests for _call_internal_tool() server_prefix parameter."""

    @pytest.fixture
    def orchestrator(self):
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.orchestrator.services import ServiceContainer

        settings = make_mock_settings(use_unified_dispatch=True)
        db_factory = MagicMock()
        return JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(),
        )

    @pytest.mark.asyncio
    async def test_server_prefix_from_registry(self, orchestrator):
        """When server_prefix is passed, it overrides _INTERNAL_TOOL_SERVER."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "send_telegram", {"text": "hi"}, server_prefix="communication"
        )
        mock_client.call_tool.assert_called_once_with("communication_send_telegram", {"text": "hi"})

    @pytest.mark.asyncio
    async def test_no_prefix_uses_internal_tool_server(self, orchestrator):
        """Without server_prefix, falls back to _INTERNAL_TOOL_SERVER dict."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool("search", {"query": "test"})
        mock_client.call_tool.assert_called_once_with("intelligence_search", {"query": "test"})

    @pytest.mark.asyncio
    async def test_server_prefix_intelligence(self, orchestrator):
        """server_prefix='intelligence' builds intelligence_search."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "search", {"query": "test"}, server_prefix="intelligence"
        )
        mock_client.call_tool.assert_called_once_with("intelligence_search", {"query": "test"})


class TestExecuteToolUnified:
    """Tests for _execute_tool_unified() 3-backend match dispatch."""

    @pytest.fixture
    def orchestrator(self):
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.orchestrator.services import ServiceContainer

        settings = make_mock_settings(use_unified_dispatch=True)
        db_factory = MagicMock()
        orch = JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(),
        )
        orch._publish_event = AsyncMock()
        return orch

    @pytest.mark.asyncio
    async def test_internal_mcp_dispatch(self, orchestrator):
        """internal_mcp backend dispatches via _call_internal_tool with server_prefix."""
        tool = _make_tool_record(name="search", backend="internal_mcp", server="intelligence")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        orchestrator._call_internal_tool = AsyncMock(return_value={"status": "ok"})

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            result = await orchestrator._execute_tool_unified(
                "search", {"query": "test"}, "usr_1", "ws_1"
            )

        assert result == {"status": "ok"}
        orchestrator._call_internal_tool.assert_called_once()
        # Verify server_prefix was passed
        call_args = orchestrator._call_internal_tool.call_args
        assert call_args.kwargs.get("server_prefix") == "intelligence"

    @pytest.mark.asyncio
    async def test_special_backend_returns_input(self, orchestrator):
        """_special server returns tool_input as-is (report_governor_verdict)."""
        tool = _make_tool_record(
            name="report_governor_verdict", backend="internal_mcp", server="_special"
        )

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            input_data = {"verdict": "approved", "reasoning": "low risk"}
            result = await orchestrator._execute_tool_unified(
                "report_governor_verdict", input_data, "usr_1", "ws_1"
            )

        assert result == input_data

    @pytest.mark.asyncio
    async def test_external_mcp_dispatch(self, orchestrator):
        """external_mcp backend dispatches via call_mcp_tool with real name."""
        tool = _make_tool_record(name="API-post-page", backend="external_mcp", server="notion")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            with patch(
                "src.connectors.mcp_bridge.call_mcp_tool", new_callable=AsyncMock
            ) as mock_mcp:
                mock_mcp.return_value = {"status": "ok", "page_id": "pg_123"}
                result = await orchestrator._execute_tool_unified(
                    "API-post-page", {"title": "Test"}, "usr_1", "ws_1"
                )

        mock_mcp.assert_called_once_with(
            "API-post-page",
            {"title": "Test", "workspace_id": "ws_1"},
            user_id="usr_1",
            workspace_id="ws_1",
        )
        assert result["page_id"] == "pg_123"

    @pytest.mark.asyncio
    async def test_composite_dispatch(self, orchestrator):
        """composite backend dispatches via _call_composite_tool."""
        tool = _make_tool_record(name="web_search", backend="composite", server="_composite")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        orchestrator._call_composite_tool = AsyncMock(return_value={"results": [{"title": "test"}]})

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            result = await orchestrator._execute_tool_unified(
                "web_search", {"query": "test"}, "usr_1", "ws_1"
            )

        assert "results" in result
        orchestrator._call_composite_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, orchestrator):
        """Unknown tool returns error dict."""
        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=None)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            result = await orchestrator._execute_tool_unified(
                "nonexistent_tool", {}, "usr_1", "ws_1"
            )

        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_disabled_tool_returns_blocked(self, orchestrator):
        """Disabled tool returns blocked error."""
        tool = _make_tool_record(name="search", enabled=False)

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            result = await orchestrator._execute_tool_unified(
                "search", {"query": "test"}, "usr_1", "ws_1"
            )

        assert "error" in result
        assert result.get("blocked") is True


class TestCanUseToolUnified:
    """Tests for SubAgent.can_use_tool_unified() — registry-driven."""

    @pytest.mark.asyncio
    async def test_matching_capability_returns_true(self):
        """Tool with matching capability in agent's scope returns True."""
        from src.orchestrator.agents import SubAgent

        agent = SubAgent(
            name="researcher",
            prompt="test",
            model_tier="sonnet",
            capability_scope={"internal.search", "search.web"},
        )
        tool = _make_tool_record(name="search", capability="internal.search")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        mock_db = AsyncMock()
        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await agent.can_use_tool_unified("search", mock_db)

        assert result is True

    @pytest.mark.asyncio
    async def test_non_matching_capability_returns_false(self):
        """Tool with capability NOT in agent's scope returns False."""
        from src.orchestrator.agents import SubAgent

        agent = SubAgent(
            name="librarian",
            prompt="test",
            model_tier="sonnet",
            capability_scope={"internal.update_entity"},
        )
        tool = _make_tool_record(name="search", capability="internal.search")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        mock_db = AsyncMock()
        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await agent.can_use_tool_unified("search", mock_db)

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_false(self):
        """Unknown tool returns False."""
        from src.orchestrator.agents import SubAgent

        agent = SubAgent(
            name="researcher",
            prompt="test",
            model_tier="sonnet",
            capability_scope={"internal.search"},
        )

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=None)

        mock_db = AsyncMock()
        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await agent.can_use_tool_unified("nonexistent", mock_db)

        assert result is False


class TestFlagGating:
    """Tests for flag-based routing in _execute_tool()."""

    @pytest.fixture
    def orchestrator(self):
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.orchestrator.services import ServiceContainer

        settings = make_mock_settings(use_unified_dispatch=False)
        db_factory = MagicMock()
        orch = JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(),
        )
        return orch

    @pytest.mark.asyncio
    async def test_flag_off_uses_old_dispatch(self, orchestrator):
        """Flag OFF: _execute_tool does NOT call _execute_tool_unified."""
        orchestrator._execute_tool_unified = AsyncMock()
        orchestrator._publish_event = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            mock_reg.is_blocked_tool = AsyncMock(return_value=False)
            mock_reg_cls.return_value = mock_reg

            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            # report_governor_verdict is the simplest path in old dispatch
            result = await orchestrator._execute_tool(
                "report_governor_verdict", {"v": "ok"}, "usr_1", "ws_1"
            )

        orchestrator._execute_tool_unified.assert_not_called()
        assert result == {"v": "ok"}

    @pytest.mark.asyncio
    async def test_flag_on_uses_unified_dispatch(self, orchestrator):
        """Flag ON: _execute_tool delegates to _execute_tool_unified."""
        orchestrator._settings.use_unified_dispatch = True
        orchestrator._execute_tool_unified = AsyncMock(return_value={"status": "ok"})

        result = await orchestrator._execute_tool("search", {"q": "test"}, "usr_1", "ws_1")

        orchestrator._execute_tool_unified.assert_called_once_with(
            "search", {"q": "test"}, "usr_1", "ws_1"
        )
        assert result == {"status": "ok"}
