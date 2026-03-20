"""Tests for tool name normalization — canonical names, deduplication, resolve."""

from unittest.mock import AsyncMock

from src.services.tool_registry import CANONICAL_ALIASES, ToolRegistry


class TestResolveCanonical:
    def test_known_aliases_resolve(self):
        db = AsyncMock()
        registry = ToolRegistry(db)

        assert registry.resolve_canonical("gmail_send_email") == "gmail_send"
        assert registry.resolve_canonical("gmail_draft") == "gmail_create_draft"
        assert registry.resolve_canonical("calendar_create") == "calendar_create_event"
        assert registry.resolve_canonical("calendar_update") == "calendar_update_event"
        assert registry.resolve_canonical("slack_post_message") == "slack_send_message"
        assert registry.resolve_canonical("calendar_delete") == "calendar_delete_event"

    def test_unknown_tool_returns_itself(self):
        db = AsyncMock()
        registry = ToolRegistry(db)

        assert registry.resolve_canonical("unknown_tool") == "unknown_tool"
        assert registry.resolve_canonical("gmail_send") == "gmail_send"

    def test_canonical_aliases_map_completeness(self):
        """All aliases should point to tools that exist in _DEFAULT_TOOLS."""
        from src.services.tool_registry import _DEFAULT_TOOLS

        tool_names = {t["name"] for t in _DEFAULT_TOOLS}

        for alias, canonical in CANONICAL_ALIASES.items():
            assert canonical in tool_names, (
                f"Alias '{alias}' points to '{canonical}' which is not in _DEFAULT_TOOLS"
            )
            assert alias in tool_names, f"Alias '{alias}' is not in _DEFAULT_TOOLS"


class TestAgentScopeDeduplication:
    def test_operator_scope_has_no_duplicates(self):
        from src.orchestrator.agents import AGENT_TOOL_SCOPES

        operator_scope = AGENT_TOOL_SCOPES["operator"]
        # Sets inherently have no dupes — verify it's actually a set
        assert isinstance(operator_scope, set)

    def test_operator_scope_uses_canonical_names(self):
        """Operator scope should prefer canonical over alias names."""
        from src.orchestrator.agents import AGENT_TOOL_SCOPES

        scope = AGENT_TOOL_SCOPES["operator"]

        # Canonical names should be present
        assert "gmail_send" in scope
        assert "gmail_create_draft" in scope
        assert "calendar_create_event" in scope
        assert "slack_send_message" in scope

        # Alias names should NOT be present
        assert "gmail_send_email" not in scope
        assert "gmail_draft" not in scope
        assert "calendar_create" not in scope
        assert "slack_post_message" not in scope


class TestDefaultToolsCanonicalNames:
    def test_alias_tools_have_canonical_name_set(self):
        from src.services.tool_registry import _DEFAULT_TOOLS

        alias_tools = {t["name"]: t for t in _DEFAULT_TOOLS if t.get("canonical_name")}

        # Known aliases should have canonical_name set
        assert alias_tools["gmail_send_email"]["canonical_name"] == "gmail_send"
        assert alias_tools["gmail_draft"]["canonical_name"] == "gmail_create_draft"
        assert alias_tools["slack_post_message"]["canonical_name"] == "slack_send_message"

    def test_missing_tools_now_registered(self):
        """push_ui_update and perplexity_search should be in _DEFAULT_TOOLS."""
        from src.services.tool_registry import _DEFAULT_TOOLS

        tool_names = {t["name"] for t in _DEFAULT_TOOLS}
        assert "push_ui_update" in tool_names
        assert "perplexity_search" in tool_names
