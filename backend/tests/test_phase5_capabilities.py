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


class TestNewBrowserCapabilities:
    def test_new_browser_capabilities_in_catalog(self):
        for cap in ("browser.execute", "browser.install", "browser.navigate_back", "browser.wait"):
            assert cap in CAPABILITY_CATALOG, f"Missing capability: {cap}"

    def test_new_playwright_tools_mapped(self):
        expected = {
            "browser_evaluate": "browser.execute",
            "browser_run_code": "browser.execute",
            "browser_install": "browser.install",
            "browser_navigate_back": "browser.navigate_back",
            "browser_take_screenshot": "browser.screenshot",
            "browser_wait_for": "browser.wait",
            "browser_fill_form": "browser.type",
        }
        for tool, cap in expected.items():
            actual = _get_cap(tool)
            assert actual == cap, f"{tool} should map to {cap}, got {actual}"

    def test_phantom_tools_removed(self):
        names = _all_catalog_names()
        assert "browser_pdf_save" not in names, "browser_pdf_save is phantom"
        assert "browser_wait" not in names, "browser_wait is wrong name"


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

    def test_operator_has_calendar_delete(self):
        assert "calendar.delete" in AGENT_CAPABILITY_SCOPES["operator"]

    def test_operator_has_workflow_delete(self):
        scope = AGENT_CAPABILITY_SCOPES["operator"]
        for cap in ("workflow.delete", "workflow.delete_comment", "workflow.delete_milestone"):
            assert cap in scope, f"Operator missing {cap}"
