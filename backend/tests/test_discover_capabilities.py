"""Tests for discover_capabilities MCP tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.schemas import DiscoverCapabilitiesInput

# ── Helpers ─────────────────────────────────────────────────────────


def _mock_tool(name, capability, description="", risk="low", approval=False):
    tool = MagicMock()
    tool.name = name
    tool.capability = capability
    tool.description = description
    tool.risk_level = risk
    tool.requires_approval = approval
    tool.enabled = True
    return tool


def _mock_ctx():
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.error = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.debug = AsyncMock()
    ctx.report_progress = AsyncMock()
    return ctx


def _configure_with_tools(tools: list) -> tuple[AsyncMock, callable]:
    from src.tools import intelligence_server

    old_db_factory = intelligence_server._db_factory
    old_settings = intelligence_server._settings
    old_services = intelligence_server._services

    mock_session = AsyncMock()
    tool_result = MagicMock()
    tool_result.scalars.return_value.all.return_value = tools
    mock_session.execute = AsyncMock(return_value=tool_result)

    mock_db_factory = MagicMock()
    async_cm = AsyncMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_session)
    async_cm.__aexit__ = AsyncMock(return_value=False)
    mock_db_factory.return_value = async_cm

    intelligence_server.configure(mock_db_factory, MagicMock(), MagicMock())

    def cleanup():
        intelligence_server._db_factory = old_db_factory
        intelligence_server._settings = old_settings
        intelligence_server._services = old_services

    return mock_session, cleanup


# ── TestDiscoverCapabilitiesInput ───────────────────────────────────


class TestDiscoverCapabilitiesInput:
    def test_valid(self):
        """DiscoverCapabilitiesInput accepts a query string."""
        inp = DiscoverCapabilitiesInput(query="email")
        assert inp.query == "email"

    def test_has_docstring(self):
        """Model docstring exists and is meaningful (>10 chars)."""
        assert DiscoverCapabilitiesInput.__doc__ is not None
        assert len(DiscoverCapabilitiesInput.__doc__.strip()) > 10

    def test_schema_has_query_field(self):
        """JSON schema has 'query' property with type 'string'."""
        schema = DiscoverCapabilitiesInput.model_json_schema()
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["type"] == "string"


# ── TestDiscoverCapabilitiesRegistration ────────────────────────────


class TestDiscoverCapabilitiesRegistration:
    def test_in_tool_input_models(self):
        """discover_capabilities is in TOOL_INPUT_MODELS and points to correct class."""
        from src.tools.schemas import TOOL_INPUT_MODELS

        assert "discover_capabilities" in TOOL_INPUT_MODELS
        assert TOOL_INPUT_MODELS["discover_capabilities"] is DiscoverCapabilitiesInput

    def test_in_internal_tools_catalog(self):
        """discover_capabilities is in catalog with correct metadata."""
        from src.tools.catalog import get_internal_tool_by_name

        tool = get_internal_tool_by_name("discover_capabilities")
        assert tool is not None
        assert tool.capability == "system.discovery"
        assert tool.risk_level == "none"
        assert tool.requires_approval is False
        assert tool.server == "intelligence"
        assert tool.read_only is True

    def test_input_model_matches_catalog(self):
        """Catalog entry's input_model IS DiscoverCapabilitiesInput."""
        from src.tools.catalog import get_internal_tool_by_name

        tool = get_internal_tool_by_name("discover_capabilities")
        assert tool is not None
        assert tool.input_model is DiscoverCapabilitiesInput


# ── TestDiscoverCapabilitiesHandler ─────────────────────────────────


class TestDiscoverCapabilitiesHandler:
    @pytest.mark.asyncio
    async def test_returns_matching_capabilities(self):
        """Query 'email' matches tools with email-related capabilities."""
        tools = [
            _mock_tool("send_email", "email.send", "Send an email"),
            _mock_tool("read_email", "email.read", "Read emails"),
            _mock_tool("draft_email", "email.draft", "Draft an email"),
            _mock_tool("get_events", "calendar.list", "List calendar events"),
        ]
        mock_db, cleanup = _configure_with_tools(tools)
        try:
            from src.tools import intelligence_server

            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(query="email", ctx=ctx)
            caps = result["capabilities"]
            cap_names = [c["capability"] for c in caps]
            assert "email.send" in cap_names
            assert "email.read" in cap_names
            assert "email.draft" in cap_names
            assert "calendar.list" not in cap_names
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_no_matches(self):
        """Query 'notion' returns empty when only email tools exist."""
        tools = [
            _mock_tool("send_email", "email.send", "Send an email"),
        ]
        mock_db, cleanup = _configure_with_tools(tools)
        try:
            from src.tools import intelligence_server

            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(query="notion", ctx=ctx)
            assert result["capabilities"] == []
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_matches_description(self):
        """Query matches against tool description text."""
        tools = [
            _mock_tool(
                "get_events",
                "calendar.list",
                "List upcoming calendar events",
            ),
        ]
        mock_db, cleanup = _configure_with_tools(tools)
        try:
            from src.tools import intelligence_server

            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(query="upcoming", ctx=ctx)
            caps = result["capabilities"]
            assert len(caps) == 1
            assert caps[0]["capability"] == "calendar.list"
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_deduplicates_capabilities(self):
        """Two tools with same capability produce 1 entry with 2 tools."""
        tools = [
            _mock_tool("search_email", "email.read", "Search emails"),
            _mock_tool("get_email", "email.read", "Get email content"),
        ]
        mock_db, cleanup = _configure_with_tools(tools)
        try:
            from src.tools import intelligence_server

            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(query="email", ctx=ctx)
            caps = result["capabilities"]
            assert len(caps) == 1
            assert caps[0]["capability"] == "email.read"
            assert len(caps[0]["tools"]) == 2
            assert "search_email" in caps[0]["tools"]
            assert "get_email" in caps[0]["tools"]
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_skips_tools_without_capability(self):
        """Tools with None capability are excluded."""
        tools = [
            _mock_tool("orphan_tool", None, "No capability"),
            _mock_tool("send_email", "email.send", "Send an email"),
        ]
        mock_db, cleanup = _configure_with_tools(tools)
        try:
            from src.tools import intelligence_server

            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(query="email", ctx=ctx)
            caps = result["capabilities"]
            cap_names = [c["capability"] for c in caps]
            assert None not in cap_names
            assert len(caps) == 1
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_includes_risk_and_status(self):
        """Returned capability includes risk, status='connected', and description."""
        tools = [
            _mock_tool("send_email", "email.send", "Send an email", risk="high", approval=True),
        ]
        mock_db, cleanup = _configure_with_tools(tools)
        try:
            from src.tools import intelligence_server

            ctx = _mock_ctx()
            result = await intelligence_server.discover_capabilities(query="email", ctx=ctx)
            caps = result["capabilities"]
            assert len(caps) == 1
            cap = caps[0]
            assert cap["risk"] == "high"
            assert cap["status"] == "connected"
            assert cap["description"] == "Send an email"
        finally:
            cleanup()
