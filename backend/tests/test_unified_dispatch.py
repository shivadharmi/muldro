"""Tests for Registry-Driven Dispatch.

Covers: unified dispatch, can_use_tool, is_auto_execute_tool,
and composite/internal/external backend routing.
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


class TestCallCompositeTool:
    """Tests for _call_composite_tool() extracted handler."""

    @pytest.fixture
    def orchestrator(self):
        """Create a minimal JarvisOrchestrator with mocked dependencies."""
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.orchestrator.services import ServiceContainer

        settings = make_mock_settings()
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

        settings = make_mock_settings()
        db_factory = MagicMock()
        return JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(),
        )

    @pytest.mark.asyncio
    async def test_server_prefix_from_registry(self, orchestrator):
        """server_prefix is required and used to namespace tool names."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "push_ui_update", {"surface_id": "daily_brief"}, server_prefix="communication"
        )
        mock_client.call_tool.assert_called_once_with(
            "communication_push_ui_update", {"surface_id": "daily_brief"}
        )

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


class TestExecuteTool:
    """Tests for _execute_tool() 3-backend match dispatch."""

    @pytest.fixture
    def orchestrator(self):
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.orchestrator.services import ServiceContainer

        settings = make_mock_settings()
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

            result = await orchestrator._execute_tool("search", {"query": "test"}, "usr_1", "ws_1")

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
            result = await orchestrator._execute_tool(
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
                result = await orchestrator._execute_tool(
                    "API-post-page", {"title": "Test"}, "usr_1", "ws_1"
                )

        mock_mcp.assert_called_once_with(
            "API-post-page",
            {"title": "Test"},
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

            result = await orchestrator._execute_tool(
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

            result = await orchestrator._execute_tool("nonexistent_tool", {}, "usr_1", "ws_1")

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

            result = await orchestrator._execute_tool("search", {"query": "test"}, "usr_1", "ws_1")

        assert "error" in result
        assert result.get("blocked") is True


class TestCanUseTool:
    """Tests for SubAgent.can_use_tool() — registry-driven."""

    @pytest.mark.asyncio
    async def test_matching_capability_returns_true(self):
        """Tool with matching capability in agent's scope returns True."""
        from src.orchestrator.agents import SubAgent

        agent = SubAgent(
            name="perceiver",
            prompt="test",
            model_tier="sonnet",
            capability_scope={"internal.search", "search.web"},
        )
        tool = _make_tool_record(name="search", capability="internal.search")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        mock_db = AsyncMock()
        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await agent.can_use_tool("search", mock_db)

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
            result = await agent.can_use_tool("search", mock_db)

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_false(self):
        """Unknown tool returns False."""
        from src.orchestrator.agents import SubAgent

        agent = SubAgent(
            name="perceiver",
            prompt="test",
            model_tier="sonnet",
            capability_scope={"internal.search"},
        )

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=None)

        mock_db = AsyncMock()
        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await agent.can_use_tool("nonexistent", mock_db)

        assert result is False


class TestIsAutoExecuteTool:
    """Tests for Governor.is_auto_execute_tool() — registry-derived."""

    @pytest.fixture
    def governor(self):
        from src.services.governor import Governor

        db = AsyncMock()
        return Governor(db=db)

    @pytest.mark.asyncio
    async def test_low_risk_no_approval_returns_true(self, governor):
        """Low risk + no approval required = auto-execute."""
        tool = _make_tool_record(risk_level="low", requires_approval=False)

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor.is_auto_execute_tool("search")

        assert result is True

    @pytest.mark.asyncio
    async def test_high_risk_returns_false(self, governor):
        """High risk = not auto-execute."""
        tool = _make_tool_record(risk_level="high", requires_approval=True)

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor.is_auto_execute_tool("sendGmailDraft")

        assert result is False

    @pytest.mark.asyncio
    async def test_low_risk_with_approval_returns_false(self, governor):
        """Low risk but requires approval = not auto-execute."""
        tool = _make_tool_record(risk_level="low", requires_approval=True)

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor.is_auto_execute_tool("approve_action")

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_false(self, governor):
        """Unknown tool = not auto-execute (safe default)."""
        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=None)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor.is_auto_execute_tool("nonexistent")

        assert result is False


class TestSessionPoolDeNormalization:
    """Tests for session pool storing real tool names (no normalization)."""

    def _make_pool(self):
        """Create a UserMCPSessionPool."""
        from src.integrations.session_pool import UserMCPSessionPool

        pool = UserMCPSessionPool()
        return pool

    @pytest.mark.asyncio
    async def test_unified_stores_real_names(self):
        """Tool names stored as-is (no normalization)."""
        pool = self._make_pool()
        pool.register_server_config("notion", {"command": "npx", "args": ["notion-mcp"]})

        mock_client = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "API-post-page"
        mock_tool.description = "Create a page"
        mock_tool.inputSchema = {"type": "object", "properties": {"title": {"type": "string"}}}
        mock_client.list_tools = AsyncMock(return_value=[mock_tool])

        with patch("src.integrations.session_pool.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_ctx

            # Mock _register_discovered_tools to avoid DB access in test
            pool._register_discovered_tools = AsyncMock()

            session = await pool.get_or_create_session("notion", "usr_1")

        # Tool stored under real name
        assert "API-post-page" in session.tools
        assert pool._tool_metadata.get("API-post-page") is not None
        assert pool._tool_metadata["API-post-page"]["name"] == "API-post-page"

    @pytest.mark.asyncio
    async def test_unified_call_tool_skips_translation(self):
        """call_tool uses tool_name directly (no canonical→raw translation)."""
        pool = self._make_pool()
        # Register server config so get_or_create_session resolves the key correctly.
        # auth_provider="none" makes effective_user="__shared__".
        pool.register_server_config("notion", {"command": "npx", "args": ["notion-mcp"]})

        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text='{"status": "ok"}')]
        mock_client.call_tool = AsyncMock(return_value=mock_result)

        from src.integrations.session_pool import SessionEntry

        session = SessionEntry(
            client=mock_client,
            client_ctx=MagicMock(),
            server_name="notion",
            user_id="__shared__",
            tools={"API-post-page": "API-post-page"},
        )
        pool._sessions[("", "notion", "__shared__")] = session

        result = await pool.call_tool(
            "API-post-page",
            {"title": "Test"},
            user_id="usr_1",
            server_name="notion",
        )

        mock_client.call_tool.assert_called_once_with("API-post-page", {"title": "Test"})
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_unified_registers_unknown_tools(self):
        """Unknown discovered tools are registered in DB."""
        pool = self._make_pool()
        pool.register_server_config("notion", {"command": "npx", "args": ["notion-mcp"]})

        mock_client = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "API-new-unknown-tool"
        mock_tool.description = "A new tool"
        mock_tool.inputSchema = {"type": "object"}
        mock_client.list_tools = AsyncMock(return_value=[mock_tool])

        with patch("src.integrations.session_pool.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_ctx

            # Mock _register_discovered_tools and verify it was called
            pool._register_discovered_tools = AsyncMock()
            await pool.get_or_create_session("notion", "usr_1")

        pool._register_discovered_tools.assert_called_once()
        call_args = pool._register_discovered_tools.call_args
        raw_tools = call_args[0][0]
        assert len(raw_tools) == 1
        assert raw_tools[0].name == "API-new-unknown-tool"
