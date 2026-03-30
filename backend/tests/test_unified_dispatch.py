"""Tests for Phase 11: Feature Flag + Registry-Driven Dispatch.

Covers: unified dispatch, can_use_tool_unified, is_auto_execute_tool,
flag gating, and composite/internal/external backend routing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


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
