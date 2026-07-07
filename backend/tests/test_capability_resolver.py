"""Tests for CapabilityResolver and route_step."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.capability_resolver import CapabilityResolver, route_step

# -- Mock helpers ----------------------------------------------------------


def _mock_tool(
    name: str,
    capability: str,
    requires_approval: bool = False,
    description: str = "",
    input_schema: dict | None = None,
) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.capability = capability
    tool.requires_approval = requires_approval
    tool.description = description or f"Mock {name}"
    tool.risk_level = "high" if requires_approval else "low"
    tool.input_schema = input_schema or {"type": "object"}
    tool.enabled = True
    return tool


def _mock_db_with_tools(tools: list[MagicMock]) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = tools
    db.execute = AsyncMock(return_value=result)
    return db


# -- TestResolve -----------------------------------------------------------


class TestResolve:
    @pytest.mark.asyncio
    async def test_single_match(self):
        tools = [
            _mock_tool("gmail_search", "email.search"),
            _mock_tool("cal_list", "calendar.list"),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        result = await resolver.resolve("email.search")
        assert len(result) == 1
        assert result[0].name == "gmail_search"

    @pytest.mark.asyncio
    async def test_multiple_matches(self):
        tools = [
            _mock_tool("gmail_read_1", "email.read"),
            _mock_tool("gmail_read_2", "email.read"),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        result = await resolver.resolve("email.read")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_match(self):
        tools = [_mock_tool("gmail_search", "email.search")]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        result = await resolver.resolve("notion.create")
        assert result == []

    @pytest.mark.asyncio
    async def test_unknown_capability(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)

        result = await resolver.resolve("unknown.thing")
        assert result == []


# -- TestResolveForStep ----------------------------------------------------


class TestResolveForStep:
    @pytest.mark.asyncio
    async def test_primary_tools_included(self):
        tools = [
            _mock_tool("gmail_send", "email.send", requires_approval=True),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        result = await resolver.resolve_for_step("email.send")
        names = [t["name"] for t in result]
        assert "gmail_send" in names

    @pytest.mark.asyncio
    async def test_related_read_tools_included(self):
        tools = [
            _mock_tool("gmail_send", "email.send", requires_approval=True),
            _mock_tool("gmail_search", "email.search", requires_approval=False),
            _mock_tool("gmail_read", "email.read", requires_approval=False),
            _mock_tool("cal_list", "calendar.list", requires_approval=False),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        result = await resolver.resolve_for_step("email.send")
        names = [t["name"] for t in result]
        assert "gmail_send" in names
        assert "gmail_search" in names
        assert "gmail_read" in names
        assert "cal_list" not in names

    @pytest.mark.asyncio
    async def test_returns_claude_api_format(self):
        tools = [
            _mock_tool(
                "gmail_send",
                "email.send",
                requires_approval=True,
                description="Send an email",
                input_schema={"type": "object", "properties": {"to": {"type": "string"}}},
            ),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        result = await resolver.resolve_for_step("email.send")
        assert len(result) == 1
        tool_dict = result[0]
        assert "name" in tool_dict
        assert "description" in tool_dict
        assert "input_schema" in tool_dict
        assert tool_dict["input_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_no_capability_match(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)

        result = await resolver.resolve_for_step("unknown.cap")
        assert result == []


# -- TestReadWriteCapability -----------------------------------------------


class TestReadWriteCapability:
    @pytest.mark.asyncio
    async def test_read_only_tools(self):
        tools = [
            _mock_tool("gmail_search", "email.search", requires_approval=False),
            _mock_tool("gmail_read", "email.read_msg", requires_approval=False),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        assert await resolver.is_read_capability("email.search") is True
        assert await resolver.is_write_capability("email.search") is False

    @pytest.mark.asyncio
    async def test_write_tools(self):
        tools = [
            _mock_tool("gmail_send", "email.send", requires_approval=True),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        assert await resolver.is_write_capability("email.send") is True
        assert await resolver.is_read_capability("email.send") is False

    @pytest.mark.asyncio
    async def test_mixed_approval(self):
        tools = [
            _mock_tool("gmail_send", "email.send", requires_approval=True),
            _mock_tool("gmail_draft", "email.send", requires_approval=False),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)

        assert await resolver.is_write_capability("email.send") is True
        assert await resolver.is_read_capability("email.send") is False

    @pytest.mark.asyncio
    async def test_unknown_capability(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)

        # all() on empty list returns True
        assert await resolver.is_read_capability("nonexistent.cap") is True
        assert await resolver.is_write_capability("nonexistent.cap") is False


# -- TestRouteStep ---------------------------------------------------------


class TestRouteStep:
    @pytest.mark.asyncio
    async def test_reason_routes_to_presenter(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)
        assert await route_step("reason", resolver) == "presenter"

    @pytest.mark.asyncio
    async def test_respond_routes_to_presenter(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)
        assert await route_step("respond", resolver) == "presenter"

    @pytest.mark.asyncio
    async def test_none_routes_to_presenter(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)
        assert await route_step("none", resolver) == "presenter"

    @pytest.mark.asyncio
    async def test_knowledge_routes_to_librarian(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)
        assert await route_step("knowledge.store", resolver) == "librarian"

    @pytest.mark.asyncio
    async def test_knowledge_search_routes_to_librarian(self):
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)
        assert await route_step("knowledge.search", resolver) == "librarian"

    @pytest.mark.asyncio
    async def test_read_capability_routes_to_perceiver(self):
        tools = [
            _mock_tool("gmail_search", "email.search", requires_approval=False),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)
        assert await route_step("email.search", resolver) == "perceiver"

    @pytest.mark.asyncio
    async def test_write_capability_routes_to_executor(self):
        tools = [
            _mock_tool("gmail_send", "email.send", requires_approval=True),
        ]
        db = _mock_db_with_tools(tools)
        resolver = CapabilityResolver(db)
        assert await route_step("email.send", resolver) == "executor"

    @pytest.mark.asyncio
    async def test_unknown_capability_returns_empty_string(self):
        """Unknown capability returns empty string (unroutable) instead of fallback."""
        db = _mock_db_with_tools([])
        resolver = CapabilityResolver(db)
        assert await route_step("totally.unknown", resolver) == ""
