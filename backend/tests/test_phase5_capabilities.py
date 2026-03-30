"""Tests for Phase 5 — missing capabilities, tool mappings, and agent scope fixes."""

from src.integrations.capabilities import (
    CAPABILITY_CATALOG,
    CapabilityFamily,
)
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


class TestFilesystemCapabilities:
    def test_filesystem_family_exists(self):
        assert hasattr(CapabilityFamily, "FILESYSTEM")
        assert CapabilityFamily.FILESYSTEM == "filesystem"

    def test_filesystem_capabilities_in_catalog(self):
        expected = [
            "filesystem.read",
            "filesystem.read_media",
            "filesystem.write",
            "filesystem.move",
            "filesystem.list",
            "filesystem.search",
        ]
        for cap in expected:
            assert cap in CAPABILITY_CATALOG, f"Missing capability: {cap}"

    def test_filesystem_tools_mapped(self):
        expected_mappings = {
            "read_text_file": "filesystem.read",
            "read_file": "filesystem.read",
            "read_media_file": "filesystem.read_media",
            "read_multiple_files": "filesystem.read",
            "write_file": "filesystem.write",
            "edit_file": "filesystem.write",
            "create_directory": "filesystem.write",
            "move_file": "filesystem.move",
            "list_directory": "filesystem.list",
            "list_directory_with_sizes": "filesystem.list",
            "directory_tree": "filesystem.list",
            "get_file_info": "filesystem.read",
            "search_files": "filesystem.search",
            "list_allowed_directories": "filesystem.list",
        }
        for tool, cap in expected_mappings.items():
            actual = _get_cap(tool)
            assert actual == cap, f"{tool} should map to {cap}, got {actual}"


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

    def test_new_linear_tools_mapped(self):
        expected = {
            "linear_create_issues": "workflow.create_issues",
            "linear_bulk_update_issues": "workflow.bulk_update",
            "linear_search_issues_by_identifier": "workflow.search_by_id",
            "linear_update_comment": "workflow.update_comment",
            "linear_delete_comment": "workflow.delete_comment",
            "linear_resolve_comment": "workflow.resolve_comment",
            "linear_unresolve_comment": "workflow.unresolve_comment",
            "linear_get_user": "workflow.get_user",
            "linear_get_project": "workflow.get_project",
            "linear_list_projects": "workflow.list_projects",
            "linear_create_project_with_issues": "workflow.create_project",
            "linear_create_project_milestone": "workflow.create_milestone",
            "linear_get_project_milestones": "workflow.get_milestones",
            "linear_update_project_milestone": "workflow.update_milestone",
            "linear_delete_project_milestone": "workflow.delete_milestone",
            "linear_create_customer_need_from_attachment": "workflow.create_customer_need",
            "linear_auth_callback": "workflow.auth",
        }
        for tool, cap in expected.items():
            actual = _get_cap(tool)
            assert actual == cap, f"{tool} should map to {cap}, got {actual}"


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
    def test_observer_has_filesystem_read(self):
        scope = AGENT_CAPABILITY_SCOPES["observer"]
        for cap in ("filesystem.read", "filesystem.list", "filesystem.search"):
            assert cap in scope, f"Observer missing {cap}"

    def test_observer_has_workflow_get_teams(self):
        assert "workflow.get_teams" in AGENT_CAPABILITY_SCOPES["observer"]

    def test_operator_has_calendar_delete(self):
        assert "calendar.delete" in AGENT_CAPABILITY_SCOPES["operator"]

    def test_operator_has_workflow_delete(self):
        scope = AGENT_CAPABILITY_SCOPES["operator"]
        for cap in ("workflow.delete", "workflow.delete_comment", "workflow.delete_milestone"):
            assert cap in scope, f"Operator missing {cap}"

    def test_researcher_has_filesystem_read(self):
        scope = AGENT_CAPABILITY_SCOPES["researcher"]
        for cap in ("filesystem.read", "filesystem.list", "filesystem.search"):
            assert cap in scope, f"Researcher missing {cap}"
