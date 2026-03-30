"""Tests for CapabilityResolver tool-to-capability mapping."""

from src.integrations.capabilities import CAPABILITY_CATALOG, TOOL_TO_CAPABILITY
from src.integrations.capability_resolver import ResolvedBackend


class TestResolvedBackend:
    def test_create(self):
        rb = ResolvedBackend(
            capability="email.send",
            backend_type="native",
            backend_ref="gmail",
            tool_name="gmail_send",
            priority=100,
        )
        assert rb.capability == "email.send"
        assert rb.backend_type == "native"
        assert rb.tool_name == "gmail_send"
        assert rb.priority == 100

    def test_optional_trust_id(self):
        rb = ResolvedBackend(
            capability="email.send",
            backend_type="mcp_official",
            backend_ref="google-workspace",
            tool_name="sendGmailDraft",
            trust_id="trs_123",
        )
        assert rb.trust_id == "trs_123"


class TestCapabilityResolverStatic:
    def test_resolve_tool_to_capability(self):
        # Test without DB — just the static mapping
        assert "email.send" == TOOL_TO_CAPABILITY.get("gmail_send")
        assert "email.list" == TOOL_TO_CAPABILITY.get("list_gmail_labels")
        assert "messaging.send" == TOOL_TO_CAPABILITY.get("slack_send_message")
        assert "issue.create" == TOOL_TO_CAPABILITY.get("github_create_issue")

    def test_capability_catalog_coverage(self):
        """Every capability referenced by TOOL_TO_CAPABILITY exists in CAPABILITY_CATALOG."""
        missing = set()
        for tool, cap in TOOL_TO_CAPABILITY.items():
            if cap not in CAPABILITY_CATALOG:
                missing.add(cap)
        assert not missing, f"Capabilities in TOOL_TO_CAPABILITY but not in CATALOG: {missing}"

    def test_all_capabilities_have_families(self):
        from src.integrations.capabilities import get_family_for_capability

        for cap in CAPABILITY_CATALOG:
            family = get_family_for_capability(cap)
            assert family is not None, f"Capability {cap} has no family"
