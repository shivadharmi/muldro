"""Tests for capability summary generator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.capability_summary import (
    _family_display_name,
    _group_by_family,
    generate_capability_summary,
)

# ── Mock helpers ──────────────────────────────────────────────────────


def _mock_tool(name: str, capability: str | None) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.capability = capability
    tool.description = f"Mock {name}"
    tool.risk_level = "low"
    tool.requires_approval = False
    tool.server = "google-workspace"
    tool.enabled = True
    return tool


def _mock_installation(server_name: str, enabled: bool = True, status: str = "active") -> MagicMock:
    inst = MagicMock()
    inst.server_name = server_name
    inst.display_name = server_name
    inst.enabled = enabled
    inst.status = status
    return inst


async def _generate_with_mocks(
    tools: list[MagicMock],
    installations: list[MagicMock],
    seed_servers: list[str] | None = None,
) -> str:
    """Helper to run generate_capability_summary with mocked DB."""
    db = AsyncMock()

    tool_result = MagicMock()
    tool_result.scalars.return_value.all.return_value = tools

    inst_result = MagicMock()
    inst_result.scalars.return_value.all.return_value = installations

    db.execute = AsyncMock(side_effect=[tool_result, inst_result])

    if seed_servers is not None:
        with patch(
            "src.orchestrator.capability_summary._get_seed_server_names",
            return_value=seed_servers,
        ):
            return await generate_capability_summary(db, "ws_test")
    return await generate_capability_summary(db, "ws_test")


# ── TestFamilyDisplayName ─────────────────────────────────────────────


class TestFamilyDisplayName:
    def test_email(self):
        assert _family_display_name("email") == "email — Gmail"

    def test_calendar(self):
        assert _family_display_name("calendar") == "calendar — Google Calendar"

    def test_repo(self):
        assert _family_display_name("repo") == "repo — GitHub"

    def test_issue(self):
        assert _family_display_name("issue") == "issue — GitHub/Atlassian"

    def test_doc(self):
        assert _family_display_name("doc") == "doc — Notion/Drive/Atlassian"

    def test_workflow(self):
        assert _family_display_name("workflow") == "workflow — Atlassian"

    def test_messaging(self):
        assert _family_display_name("messaging") == "messaging — Slack"

    def test_browser(self):
        assert _family_display_name("browser") == "browser — Playwright"

    def test_search(self):
        assert _family_display_name("search") == "search — Web"

    def test_filesystem(self):
        assert _family_display_name("filesystem") == "filesystem — Local Files"

    def test_internal(self):
        assert _family_display_name("internal") == "internal"

    def test_system(self):
        assert _family_display_name("system") == "system"

    def test_unknown_passes_through(self):
        assert _family_display_name("custom_thing") == "custom_thing"


# ── TestGroupByFamily ─────────────────────────────────────────────────


class TestGroupByFamily:
    def test_groups_capabilities(self):
        tools = [
            _mock_tool("gmail_search", "email.search"),
            _mock_tool("gmail_read", "email.read"),
            _mock_tool("cal_list", "calendar.list"),
        ]
        result = _group_by_family(tools)
        assert set(result["email"]) == {"search", "read"}
        assert result["calendar"] == ["list"]

    def test_skips_tools_without_capability(self):
        tools = [
            _mock_tool("gmail_search", "email.search"),
            _mock_tool("unknown_tool", None),
        ]
        result = _group_by_family(tools)
        assert "email" in result
        assert len(result) == 1

    def test_deduplicates_actions(self):
        tools = [
            _mock_tool("gmail_search_1", "email.search"),
            _mock_tool("gmail_search_2", "email.search"),
        ]
        result = _group_by_family(tools)
        assert result["email"] == ["search"]

    def test_empty_tools(self):
        result = _group_by_family([])
        assert result == {}


# ── TestGenerateCapabilitySummary ─────────────────────────────────────


class TestGenerateCapabilitySummary:
    @pytest.mark.asyncio
    async def test_connected_services(self):
        tools = [
            _mock_tool("gmail_search", "email.search"),
            _mock_tool("gmail_read", "email.read"),
            _mock_tool("cal_list", "calendar.list"),
        ]
        installations = [_mock_installation("google-workspace")]
        result = await _generate_with_mocks(tools, installations, seed_servers=[])
        assert "<connected_services>" in result
        assert "email — Gmail" in result
        assert "calendar — Google Calendar" in result

    @pytest.mark.asyncio
    async def test_disconnected_services(self):
        tools = []
        installations = []
        result = await _generate_with_mocks(tools, installations, seed_servers=["github", "slack"])
        assert "<disconnected_services>" in result
        assert "github" in result
        assert "slack" in result

    @pytest.mark.asyncio
    async def test_empty_workspace(self):
        result = await _generate_with_mocks([], [], seed_servers=[])
        # Should still produce valid output but no connected services content
        assert "<connected_services>" in result

    @pytest.mark.asyncio
    async def test_internal_tools_excluded(self):
        tools = [
            _mock_tool("search", "internal.search"),
            _mock_tool("gmail_search", "email.search"),
        ]
        installations = [_mock_installation("google-workspace")]
        result = await _generate_with_mocks(tools, installations, seed_servers=[])
        assert "internal" not in result
        assert "email — Gmail" in result
