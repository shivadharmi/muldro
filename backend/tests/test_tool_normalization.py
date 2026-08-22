"""Tests for tool name normalization — agent scope deduplication and catalog consistency."""

from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS


class TestAgentScopeDeduplication:
    def test_executor_capability_scope_has_no_duplicates(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        executor_scope = AGENT_CAPABILITY_SCOPES["executor"]
        # Sets inherently have no dupes — verify it's actually a set
        assert isinstance(executor_scope, set)

    def test_executor_capability_scope_has_expected_capabilities(self):
        """Executor scope should have read + write capabilities for autonomous tool use."""
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["executor"]

        # Write capabilities
        assert "email.send" in scope
        assert "email.draft" in scope
        assert "calendar.create" in scope
        assert "messaging.send" in scope
        assert "issue.create" in scope
        assert "doc.create" in scope

        # Read capabilities (needed for context gathering before writes)
        # Email
        assert "email.read" in scope
        assert "email.list" in scope
        assert "email.search" in scope
        # Calendar
        assert "calendar.list" in scope
        assert "calendar.get" in scope
        # Messaging
        assert "messaging.list_channels" in scope
        assert "messaging.get_history" in scope
        assert "messaging.get_thread" in scope
        # Issues
        assert "issue.list" in scope
        assert "issue.get" in scope
        assert "issue.search" in scope
        # Repos
        assert "repo.list_prs" in scope
        assert "repo.get_diff" in scope
        assert "repo.get_reviews" in scope
        # Workflow
        assert "workflow.list" in scope
        assert "workflow.get" in scope
        assert "workflow.search" in scope


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
        """store_memory and web_search should be in the catalog."""
        internal_names = {t.name for t in INTERNAL_TOOLS}
        external_names = {s.name for s in EXTERNAL_TOOL_SEEDS}
        all_names = internal_names | external_names
        assert "store_memory" in all_names
        assert "web_search" in all_names
