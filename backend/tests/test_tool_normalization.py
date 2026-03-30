"""Tests for tool name normalization — agent scope deduplication and catalog consistency."""

from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS


class TestAgentScopeDeduplication:
    def test_operator_capability_scope_has_no_duplicates(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        operator_scope = AGENT_CAPABILITY_SCOPES["operator"]
        # Sets inherently have no dupes — verify it's actually a set
        assert isinstance(operator_scope, set)

    def test_operator_capability_scope_has_expected_capabilities(self):
        """Operator scope should have write capabilities."""
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["operator"]

        assert "email.send" in scope
        assert "email.draft" in scope
        assert "calendar.create" in scope
        assert "messaging.send" in scope
        assert "issue.create" in scope
        assert "doc.create" in scope


class TestCatalogToolsHaveCapabilities:
    def test_all_internal_tools_have_capabilities(self):
        """Every internal tool should have a non-empty capability."""
        for tool in INTERNAL_TOOLS:
            assert tool.capability, f"Internal tool '{tool.name}' has no capability"

    def test_all_external_seeds_have_capabilities(self):
        """Every external tool seed should have a non-empty capability."""
        for seed in EXTERNAL_TOOL_SEEDS:
            assert seed.capability, f"External seed '{seed.name}' has no capability"

    def test_catalog_has_expected_tools(self):
        """push_ui_update and web_search should be in the catalog."""
        internal_names = {t.name for t in INTERNAL_TOOLS}
        external_names = {s.name for s in EXTERNAL_TOOL_SEEDS}
        all_names = internal_names | external_names
        assert "push_ui_update" in all_names
        assert "web_search" in all_names
