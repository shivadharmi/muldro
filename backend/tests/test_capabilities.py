"""Tests for the canonical capability catalog."""

from src.integrations.capabilities import (
    CAPABILITY_CATALOG,
    TOOL_TO_CAPABILITY,
    CapabilityFamily,
    get_capability_for_tool,
    get_family_for_capability,
    is_read_only_capability,
)


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
        valid_risks = {"low", "medium", "high", "critical"}
        for cap, meta in CAPABILITY_CATALOG.items():
            assert meta.risk_level in valid_risks, (
                f"Capability '{cap}' has invalid risk_level: {meta.risk_level}"
            )

    def test_read_only_capabilities_are_low_risk(self):
        for cap, meta in CAPABILITY_CATALOG.items():
            if meta.read_only:
                assert meta.risk_level == "low", (
                    f"Read-only capability '{cap}' should be low risk, got {meta.risk_level}"
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
            }


class TestToolToCapability:
    """Test TOOL_TO_CAPABILITY mapping completeness."""

    def test_all_tools_map_to_valid_capabilities(self):
        for tool, cap in TOOL_TO_CAPABILITY.items():
            assert cap in CAPABILITY_CATALOG, f"Tool '{tool}' maps to unknown capability '{cap}'"

    def test_gmail_tools_map_to_email(self):
        gmail_tools = [t for t in TOOL_TO_CAPABILITY if "gmail" in t.lower()]
        assert len(gmail_tools) >= 5
        for tool in gmail_tools:
            cap = TOOL_TO_CAPABILITY[tool]
            assert cap.startswith("email."), f"Gmail tool '{tool}' maps to '{cap}'"

    def test_calendar_tools_map_to_calendar(self):
        cal_tools = [t for t in TOOL_TO_CAPABILITY if "calendar" in t.lower()]
        assert len(cal_tools) >= 5
        for tool in cal_tools:
            cap = TOOL_TO_CAPABILITY[tool]
            assert cap.startswith("calendar."), f"Calendar tool '{tool}' maps to '{cap}'"

    def test_github_mcp_tools_mapped(self):
        github_mcp_tools = [
            "issue_write",
            "create_pull_request",
            "merge_pull_request",
            "search_code",
            "search_repositories",
        ]
        for tool in github_mcp_tools:
            assert tool in TOOL_TO_CAPABILITY, f"GitHub MCP tool '{tool}' not mapped"

    def test_slack_tools_map_to_messaging(self):
        slack_tools = [t for t in TOOL_TO_CAPABILITY if t.startswith("slack_")]
        assert len(slack_tools) >= 5
        for tool in slack_tools:
            cap = TOOL_TO_CAPABILITY[tool]
            assert cap.startswith("messaging."), f"Slack tool '{tool}' maps to '{cap}'"

    def test_mapping_not_empty(self):
        assert len(TOOL_TO_CAPABILITY) > 100


class TestHelpers:
    def test_get_capability_for_tool_known(self):
        assert get_capability_for_tool("gmail_send") == "email.send"

    def test_get_capability_for_tool_unknown(self):
        assert get_capability_for_tool("nonexistent_tool") is None

    def test_get_family_for_capability(self):
        assert get_family_for_capability("email.send") == CapabilityFamily.EMAIL
        assert get_family_for_capability("repo.create_pr") == CapabilityFamily.REPO

    def test_get_family_for_unknown(self):
        assert get_family_for_capability("unknown.thing") is None

    def test_is_read_only(self):
        assert is_read_only_capability("email.list") is True
        assert is_read_only_capability("email.send") is False
