"""Tests for Phase 5 — missing capabilities, tool mappings, and agent scope fixes."""

from src.integrations.capabilities import CAPABILITY_CATALOG
from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES
from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS


def _get_cap(tool_name: str) -> str | None:
    """Look up tool capability from catalog."""
    for t in INTERNAL_TOOLS:
        if t.name == tool_name:
            return t.capability
    for s in EXTERNAL_TOOL_SEEDS:
        if s.name == tool_name:
            return s.capability
    return None


def _all_catalog_names() -> set[str]:
    """Return all tool names from the catalog."""
    return {t.name for t in INTERNAL_TOOLS} | {s.name for s in EXTERNAL_TOOL_SEEDS}


class TestBrowserFamilyIsGone:
    """The Playwright MCP server and the whole browser capability family were removed.

    This replaces the pair of tests that pinned the browser capabilities and the
    browser_* tool mappings. Inverting them rather than deleting them keeps the fence:
    re-adding a browser_* seed without its capability, or a capability with no server to
    serve it, is exactly the half-migration this file existed to catch.
    """

    def test_no_browser_capabilities_remain(self):
        assert not [c for c in CAPABILITY_CATALOG if c.startswith("browser.")]

    def test_no_browser_tools_remain(self):
        assert not [n for n in _all_catalog_names() if n.startswith("browser_")]


class TestNewWorkflowCapabilities:
    def test_new_workflow_capabilities_in_catalog(self):
        new_caps = [
            "workflow.create_issues",
            "workflow.bulk_update",
            "workflow.search_by_id",
            "workflow.update_comment",
            "workflow.delete_comment",
            "workflow.resolve_comment",
            "workflow.unresolve_comment",
            "workflow.get_user",
            "workflow.get_project",
            "workflow.list_projects",
            "workflow.create_project",
            "workflow.create_milestone",
            "workflow.get_milestones",
            "workflow.update_milestone",
            "workflow.delete_milestone",
            "workflow.create_customer_need",
            "workflow.auth",
        ]
        for cap in new_caps:
            assert cap in CAPABILITY_CATALOG, f"Missing capability: {cap}"


class TestNewDocCapabilities:
    def test_new_doc_capabilities_in_catalog(self):
        new_caps = [
            "doc.get_property",
            "doc.get_comment",
            "doc.get_children",
            "doc.get_block",
            "doc.update_block",
            "doc.delete_block",
            "doc.move",
            "doc.get_database",
            "doc.create_datasource",
            "doc.get_datasource",
            "doc.update_datasource",
            "doc.list_templates",
            "doc.get_self",
            "doc.get_user",
            "doc.get_users",
        ]
        for cap in new_caps:
            assert cap in CAPABILITY_CATALOG, f"Missing capability: {cap}"

    def test_new_notion_tools_mapped(self):
        expected = {
            "API-retrieve-a-page-property": "doc.get_property",
            "API-retrieve-a-comment": "doc.get_comment",
            "API-get-block-children": "doc.get_children",
            "API-retrieve-a-block": "doc.get_block",
            "API-update-a-block": "doc.update_block",
            "API-delete-a-block": "doc.delete_block",
            "API-move-page": "doc.move",
            "API-retrieve-a-database": "doc.get_database",
            "API-create-a-data-source": "doc.create_datasource",
            "API-retrieve-a-data-source": "doc.get_datasource",
            "API-update-a-data-source": "doc.update_datasource",
            "API-list-data-source-templates": "doc.list_templates",
            "API-get-self": "doc.get_self",
            "API-get-user": "doc.get_user",
            "API-get-users": "doc.get_users",
            "API-post-search": "doc.search",
        }
        for tool, cap in expected.items():
            actual = _get_cap(tool)
            assert actual == cap, f"{tool} should map to {cap}, got {actual}"


class TestAgentScopeFixes:
    def test_perceiver_has_workflow_get_teams(self):
        assert "workflow.get_teams" in AGENT_CAPABILITY_SCOPES["perceiver"]

    def test_executor_has_calendar_delete(self):
        assert "calendar.delete" in AGENT_CAPABILITY_SCOPES["executor"]

    def test_executor_has_workflow_delete(self):
        scope = AGENT_CAPABILITY_SCOPES["executor"]
        for cap in ("workflow.delete", "workflow.delete_comment", "workflow.delete_milestone"):
            assert cap in scope, f"Executor missing {cap}"
