"""Tests for the canonical capability catalog."""

from src.integrations.capabilities import (
    CAPABILITY_CATALOG,
    CapabilityFamily,
    get_family_for_capability,
    is_read_only_capability,
)
from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS


class TestCapabilityCatalog:
    """Test CAPABILITY_CATALOG completeness and consistency."""

    def test_all_families_have_at_least_one_capability(self):
        families_used = {meta.family for meta in CAPABILITY_CATALOG.values()}
        for family in CapabilityFamily:
            assert family in families_used, f"Family {family} has no capabilities"

    def test_all_capabilities_follow_dot_notation(self):
        for cap in CAPABILITY_CATALOG:
            assert "." in cap, f"Capability '{cap}' missing dot notation"
            parts = cap.split(".")
            assert len(parts) == 2, f"Capability '{cap}' should have exactly one dot"

    def test_capability_meta_has_valid_risk_levels(self):
        valid_risks = {"none", "low", "medium", "high", "critical"}
        for cap, meta in CAPABILITY_CATALOG.items():
            assert meta.risk_level in valid_risks, (
                f"Capability '{cap}' has invalid risk_level: {meta.risk_level}"
            )

    def test_read_only_capabilities_are_safe_risk(self):
        for cap, meta in CAPABILITY_CATALOG.items():
            if meta.read_only:
                assert meta.risk_level in ("none", "low"), (
                    f"Read-only capability '{cap}' should be none/low risk, got {meta.risk_level}"
                )

    def test_catalog_not_empty(self):
        assert len(CAPABILITY_CATALOG) > 50

    def test_capability_families_are_valid(self):
        for cap, meta in CAPABILITY_CATALOG.items():
            prefix = cap.split(".")[0]
            assert meta.family == prefix or prefix in {
                "doc",
                "search",
                "browser",
                "internal",
                "messaging",
                "email",
                "calendar",
                "repo",
                "issue",
                "workflow",
                "system",
            }


class TestToolToCapability:
    """Test catalog tool-to-capability mapping completeness."""

    def _build_tool_to_cap(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for t in INTERNAL_TOOLS:
            mapping[t.name] = t.capability
        for s in EXTERNAL_TOOL_SEEDS:
            mapping[s.name] = s.capability
        return mapping

    def test_all_tools_map_to_valid_capabilities(self):
        tool_to_cap = self._build_tool_to_cap()
        for tool, cap in tool_to_cap.items():
            assert cap in CAPABILITY_CATALOG, f"Tool '{tool}' maps to unknown capability '{cap}'"

    def test_gmail_tools_map_to_email(self):
        tool_to_cap = self._build_tool_to_cap()
        gmail_tools = [t for t in tool_to_cap if "gmail" in t.lower()]
        assert len(gmail_tools) >= 5
        for tool in gmail_tools:
            cap = tool_to_cap[tool]
            assert cap.startswith("email."), f"Gmail tool '{tool}' maps to '{cap}'"

    def test_calendar_capabilities_exist(self):
        """Calendar capabilities should be present in the catalog tools."""
        tool_to_cap = self._build_tool_to_cap()
        calendar_caps = [cap for cap in tool_to_cap.values() if cap.startswith("calendar.")]
        assert len(calendar_caps) >= 2, "Should have at least 2 calendar-capability tools"

    def test_github_mcp_tools_mapped(self):
        """GitHub is gateway-only; tool names are derived from PROVIDER_REGISTRY."""
        tool_to_cap = self._build_tool_to_cap()
        github_mcp_tools = [
            "github_create_issue",
            "github_create_pull_request",
            "github_search_code",
            "github_search_repositories",
        ]
        for tool in github_mcp_tools:
            assert tool in tool_to_cap, f"GitHub MCP tool '{tool}' not mapped"

    def test_slack_tools_map_to_messaging(self):
        tool_to_cap = self._build_tool_to_cap()
        slack_tools = [t for t in tool_to_cap if t.startswith("slack_")]
        assert len(slack_tools) >= 5
        for tool in slack_tools:
            cap = tool_to_cap[tool]
            assert cap.startswith("messaging."), f"Slack tool '{tool}' maps to '{cap}'"

    def test_mapping_not_empty(self):
        # A loose floor, not a count: this asserts the catalog loaded and mapped, and must
        # not need editing every time a tool is added or a server is dropped. The exact
        # per-server counts are pinned in test_catalog.py, which is where they belong.
        tool_to_cap = self._build_tool_to_cap()
        assert len(tool_to_cap) > 50


class TestHelpers:
    def test_get_capability_via_catalog(self):
        """Internal tool 'search' should map to 'internal.search' via catalog."""
        for t in INTERNAL_TOOLS:
            if t.name == "search":
                assert t.capability == "internal.search"
                break
        else:
            raise AssertionError("search tool not in INTERNAL_TOOLS")

    def test_unknown_tool_not_in_catalog(self):
        all_names = {t.name for t in INTERNAL_TOOLS} | {s.name for s in EXTERNAL_TOOL_SEEDS}
        assert "nonexistent_tool" not in all_names

    def test_get_family_for_capability(self):
        assert get_family_for_capability("email.send") == CapabilityFamily.EMAIL
        assert get_family_for_capability("repo.create_pr") == CapabilityFamily.REPO

    def test_get_family_for_unknown(self):
        assert get_family_for_capability("unknown.thing") is None

    def test_is_read_only(self):
        assert is_read_only_capability("email.list") is True
        assert is_read_only_capability("email.send") is False
