"""Tests for browser tools integration — Playwright MCP + web_search composite."""

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID

# Patch targets — web_search.py imports at call time from src.connectors.mcp_bridge
_PATCH_CALL = "src.connectors.mcp_bridge.call_mcp_tool"
_PATCH_IS = "src.connectors.mcp_bridge.is_mcp_tool"


# ── Capability mapping tests ───────────────────────────────────────────────


class TestCapabilityMappings:
    """Verify Playwright MCP tool names are mapped to capabilities in catalog."""

    def _get_cap(self, tool_name: str) -> str | None:
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS

        for t in INTERNAL_TOOLS:
            if t.name == tool_name:
                return t.capability
        for s in EXTERNAL_TOOL_SEEDS:
            if s.name == tool_name:
                return s.capability
        return None

    def test_browser_navigate_maps_to_browser_open(self):
        assert self._get_cap("browser_navigate") == "browser.open"

    def test_web_search_maps_to_search_web(self):
        assert self._get_cap("web_search") == "search.web"

    def test_browser_tabs_mapped(self):
        assert self._get_cap("browser_tabs") == "browser.open"

    def test_browser_press_key_mapped(self):
        assert self._get_cap("browser_press_key") == "browser.type"

    def test_all_playwright_mcp_tools_mapped(self):
        """All known @playwright/mcp tool names have capability mappings."""
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS

        catalog_names = {s.name for s in EXTERNAL_TOOL_SEEDS}

        playwright_tools = [
            "browser_navigate",
            "browser_tabs",
            "browser_press_key",
            "browser_select_option",
            "browser_hover",
            "browser_drag",
            "browser_handle_dialog",
            "browser_file_upload",
            "browser_close",
            "browser_resize",
            "browser_network_requests",
            "browser_console_messages",
            # New Phase 5 tools
            "browser_evaluate",
            "browser_run_code",
            "browser_install",
            "browser_navigate_back",
            "browser_take_screenshot",
            "browser_wait_for",
            "browser_fill_form",
        ]
        for tool_name in playwright_tools:
            assert tool_name in catalog_names, f"{tool_name} not mapped"


# ── Agent capability scope tests ───────────────────────────────────────────


class TestPerceiverAgentScope:
    """Verify the Perceiver agent can use web search and browser tools."""

    @pytest.mark.asyncio
    async def test_perceiver_can_use_web_search(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.orchestrator.agents import AGENTS

        perceiver = AGENTS["perceiver"]
        mock_db = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            tool = MagicMock()
            tool.name = "web_search"
            tool.capability = "search.web"
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg

            assert await perceiver.can_use_tool("web_search", mock_db)

    @pytest.mark.asyncio
    async def test_perceiver_can_use_browser_navigate(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.orchestrator.agents import AGENTS

        perceiver = AGENTS["perceiver"]
        mock_db = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            tool = MagicMock()
            tool.name = "browser_navigate"
            tool.capability = "browser.open"
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg

            assert await perceiver.can_use_tool("browser_navigate", mock_db)

    @pytest.mark.asyncio
    async def test_perceiver_can_use_browser_snapshot(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.orchestrator.agents import AGENTS

        perceiver = AGENTS["perceiver"]
        mock_db = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            tool = MagicMock()
            tool.name = "browser_snapshot"
            tool.capability = "browser.snapshot"
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg

            assert await perceiver.can_use_tool("browser_snapshot", mock_db)

    @pytest.mark.asyncio
    async def test_perceiver_can_use_browser_screenshot(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.orchestrator.agents import AGENTS

        perceiver = AGENTS["perceiver"]
        mock_db = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            tool = MagicMock()
            tool.name = "browser_screenshot"
            tool.capability = "browser.screenshot"
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg

            assert await perceiver.can_use_tool("browser_screenshot", mock_db)

    @pytest.mark.asyncio
    async def test_perceiver_cannot_use_browser_submit(self):
        """Perceiver is read-only — no write-capable browser tools."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.orchestrator.agents import AGENTS

        perceiver = AGENTS["perceiver"]
        mock_db = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            tool = MagicMock()
            tool.name = "browser_file_upload"
            tool.capability = "browser.file_upload"
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg

            assert not await perceiver.can_use_tool("browser_file_upload", mock_db)


# ── web_search function tests ─────────────────────────────────────────────


class TestWebSearch:
    """Test the composite web_search function."""

    @pytest.mark.asyncio
    async def test_web_search_returns_structured_results(self):
        snapshot_text = (
            '- link "Python Tutorial" [ref=e5] -> https://python.org/tutorial\n'
            '  - text "Learn Python programming step by step"\n'
            '- link "Real Python" [ref=e8] -> https://realpython.com\n'
            '  - text "Python tutorials, guides and articles"\n'
        )
        mock_call = AsyncMock(
            side_effect=[
                {"status": "ok"},  # browser_navigate
                {"content": snapshot_text},  # browser_snapshot
            ]
        )
        with (
            patch(_PATCH_CALL, mock_call),
            patch(_PATCH_IS, return_value=True),
        ):
            from src.browser.web_search import web_search

            result = await web_search(
                query="python tutorial",
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

        assert result["status"] == "ok"
        assert result["provider"] == "duckduckgo"
        assert result["query"] == "python tutorial"
        assert len(result["results"]) == 2
        assert result["results"][0]["title"] == "Python Tutorial"
        assert result["results"][0]["url"] == "https://python.org/tutorial"

    @pytest.mark.asyncio
    async def test_web_search_empty_query_returns_error(self):
        from src.browser.web_search import web_search

        result = await web_search(query="", user_id=TEST_USER_ID)
        assert result["status"] == "error"
        assert "query is required" in result["error"]

    @pytest.mark.asyncio
    async def test_web_search_mcp_unavailable(self):
        with patch(_PATCH_IS, return_value=False):
            from src.browser.web_search import web_search

            result = await web_search(
                query="test", user_id=TEST_USER_ID, workspace_id=TEST_WORKSPACE_ID
            )

        assert result["status"] == "error"
        assert "Playwright MCP server not available" in result["error"]

    @pytest.mark.asyncio
    async def test_web_search_handles_empty_snapshot(self):
        mock_call = AsyncMock(
            side_effect=[
                {"status": "ok"},  # browser_navigate
                {"content": ""},  # browser_snapshot — empty
            ]
        )
        with (
            patch(_PATCH_CALL, mock_call),
            patch(_PATCH_IS, return_value=True),
        ):
            from src.browser.web_search import web_search

            result = await web_search(
                query="obscure query",
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

        assert result["status"] == "ok"
        assert result["results"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_web_search_caps_num_results(self):
        """num_results is capped at 20."""
        snapshot_text = "\n".join(
            f'- link "Result {i}" [ref=e{i}] -> https://example.com/{i}\n  - text "Snippet {i}"'
            for i in range(25)
        )
        mock_call = AsyncMock(
            side_effect=[
                {"status": "ok"},
                {"content": snapshot_text},
            ]
        )
        with (
            patch(_PATCH_CALL, mock_call),
            patch(_PATCH_IS, return_value=True),
        ):
            from src.browser.web_search import web_search

            result = await web_search(
                query="many results",
                num_results=50,  # requesting 50 but should cap at 20
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

        assert result["total"] <= 20


# ── Snapshot parsing tests ─────────────────────────────────────────────────


class TestSnapshotParsing:
    """Test the DuckDuckGo snapshot parsing logic."""

    def test_parse_filters_duckduckgo_internal_links(self):
        from src.browser.web_search import _parse_snapshot

        snapshot = (
            '- link "DuckDuckGo" [ref=e1] -> https://duckduckgo.com\n'
            '- link "Real Result" [ref=e2] -> https://example.com/article\n'
            '  - text "A real search result"\n'
        )
        results = _parse_snapshot(snapshot, max_results=10)
        assert len(results) == 1
        assert results[0]["title"] == "Real Result"

    def test_parse_extracts_snippets(self):
        from src.browser.web_search import _parse_snapshot

        snapshot = (
            '- link "Test Page" [ref=e5] -> https://test.com\n'
            '  - text "This is the snippet text for the result"\n'
        )
        results = _parse_snapshot(snapshot, max_results=10)
        assert len(results) == 1
        assert "snippet text" in results[0]["snippet"]

    def test_parse_empty_snapshot(self):
        from src.browser.web_search import _parse_snapshot

        assert _parse_snapshot("", 10) == []
        assert _parse_snapshot(None, 10) == []
