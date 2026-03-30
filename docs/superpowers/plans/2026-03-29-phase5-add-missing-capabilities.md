# Phase 5: Add Missing Capabilities + Fix Agent Scopes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ~30 new capabilities to CAPABILITY_CATALOG, ~40 new entries to TOOL_TO_CAPABILITY for live-verified tools missing mappings, fix 3 incorrect Playwright tool name mappings, and add filesystem/calendar.delete/workflow.delete capabilities to agent scopes.

**Architecture:** Additive changes to capabilities.py and agents.py. No structural changes.

**Tech Stack:** Python, pytest

---

## Summary of What's Missing

**New CapabilityFamily:** `FILESYSTEM`

**New capabilities for CAPABILITY_CATALOG (~30):**
- 6 filesystem capabilities
- 4 browser capabilities (execute, install, navigate_back, wait)
- 17 workflow/linear capabilities
- 10 doc/notion capabilities

**New TOOL_TO_CAPABILITY entries (~40):**
- 14 Filesystem tools (all unmapped)
- 5 new Playwright tools + 3 name fixes
- 17 new Linear tools
- 16 new Notion tools

**Agent scope fixes:**
- Observer: add `filesystem.read`, `filesystem.list`, `filesystem.search`, `workflow.get_teams`
- Operator: add `calendar.delete`, `workflow.delete`, `workflow.delete_comment`, `workflow.delete_milestone`
- Researcher: add `filesystem.read`, `filesystem.list`, `filesystem.search`

**Playwright name fixes in TOOL_TO_CAPABILITY:**
- `browser_wait` → remove (wrong name, not a real tool)
- `browser_pdf_save` → remove (phantom tool, doesn't exist)
- Add `browser_take_screenshot` → `browser.screenshot` (replaces wrong `browser_screenshot` mapping to Playwright)
- Add `browser_wait_for` → `browser.wait`
- Add `browser_fill_form` → `browser.type`

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `backend/src/integrations/capabilities.py` | Modify | Add FILESYSTEM family, ~30 new CAPABILITY_CATALOG entries, ~40 new TOOL_TO_CAPABILITY entries, fix 3 Playwright names |
| `backend/src/orchestrator/agents.py` | Modify | Add filesystem/calendar.delete/workflow.delete to scopes |
| `backend/tests/test_phase5_capabilities.py` | Create | Tests for new capabilities and scope fixes |

---

### Task 1: Create tests (RED)

**Files:**
- Create: `backend/tests/test_phase5_capabilities.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for Phase 5 — missing capabilities, tool mappings, and agent scope fixes."""

from src.integrations.capabilities import (
    CAPABILITY_CATALOG,
    TOOL_TO_CAPABILITY,
    CapabilityFamily,
    get_capability_for_tool,
)
from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES


class TestFilesystemCapabilities:
    def test_filesystem_family_exists(self):
        assert hasattr(CapabilityFamily, "FILESYSTEM")
        assert CapabilityFamily.FILESYSTEM == "filesystem"

    def test_filesystem_capabilities_in_catalog(self):
        expected = [
            "filesystem.read", "filesystem.read_media", "filesystem.write",
            "filesystem.move", "filesystem.list", "filesystem.search",
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
            actual = get_capability_for_tool(tool)
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
            actual = get_capability_for_tool(tool)
            assert actual == cap, f"{tool} should map to {cap}, got {actual}"

    def test_phantom_tools_removed(self):
        assert "browser_pdf_save" not in TOOL_TO_CAPABILITY, "browser_pdf_save is phantom"
        assert "browser_wait" not in TOOL_TO_CAPABILITY, "browser_wait is wrong name"


class TestNewWorkflowCapabilities:
    def test_new_workflow_capabilities_in_catalog(self):
        new_caps = [
            "workflow.create_issues", "workflow.bulk_update", "workflow.search_by_id",
            "workflow.update_comment", "workflow.delete_comment", "workflow.resolve_comment",
            "workflow.unresolve_comment", "workflow.get_user", "workflow.get_project",
            "workflow.list_projects", "workflow.create_project", "workflow.create_milestone",
            "workflow.get_milestones", "workflow.update_milestone", "workflow.delete_milestone",
            "workflow.create_customer_need", "workflow.auth",
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
            actual = get_capability_for_tool(tool)
            assert actual == cap, f"{tool} should map to {cap}, got {actual}"


class TestNewDocCapabilities:
    def test_new_doc_capabilities_in_catalog(self):
        new_caps = [
            "doc.get_property", "doc.get_comment", "doc.get_children", "doc.get_block",
            "doc.update_block", "doc.delete_block", "doc.move", "doc.get_database",
            "doc.create_datasource", "doc.get_datasource", "doc.update_datasource",
            "doc.list_templates", "doc.get_self", "doc.get_user", "doc.get_users",
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
            actual = get_capability_for_tool(tool)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_phase5_capabilities.py -v`

Expected: Many failures (missing FILESYSTEM family, missing capabilities, unmapped tools, missing scopes).

---

### Task 2: Add CapabilityFamily.FILESYSTEM + new CAPABILITY_CATALOG entries

**Files:**
- Modify: `backend/src/integrations/capabilities.py`

- [ ] **Step 3: Add FILESYSTEM to CapabilityFamily enum**

Add after `INTERNAL = "internal"`:
```python
    FILESYSTEM = "filesystem"
```

- [ ] **Step 4: Add new capabilities to CAPABILITY_CATALOG**

Add these sections to CAPABILITY_CATALOG before the closing `}`:

```python
    # Filesystem
    "filesystem.read": _cap(CapabilityFamily.FILESYSTEM, True),
    "filesystem.read_media": _cap(CapabilityFamily.FILESYSTEM, True),
    "filesystem.write": _cap(CapabilityFamily.FILESYSTEM, False, "high"),
    "filesystem.move": _cap(CapabilityFamily.FILESYSTEM, False, "high"),
    "filesystem.list": _cap(CapabilityFamily.FILESYSTEM, True),
    "filesystem.search": _cap(CapabilityFamily.FILESYSTEM, True),
    # Browser (new — additions to existing browser family)
    "browser.execute": _cap(CapabilityFamily.BROWSER, False, "high"),
    "browser.install": _cap(CapabilityFamily.BROWSER, False, "medium"),
    "browser.navigate_back": _cap(CapabilityFamily.BROWSER, True),
    "browser.wait": _cap(CapabilityFamily.BROWSER, True),
    # Workflow (new — additions to existing workflow family)
    "workflow.create_issues": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.bulk_update": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.search_by_id": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.update_comment": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.delete_comment": _cap(CapabilityFamily.WORKFLOW, False, "high"),
    "workflow.resolve_comment": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.unresolve_comment": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.get_user": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.get_project": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.list_projects": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.create_project": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.create_milestone": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.get_milestones": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.update_milestone": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.delete_milestone": _cap(CapabilityFamily.WORKFLOW, False, "high"),
    "workflow.create_customer_need": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.auth": _cap(CapabilityFamily.WORKFLOW, True),
    # Doc (new — additions to existing doc family)
    "doc.get_property": _cap(CapabilityFamily.DOC, True),
    "doc.get_comment": _cap(CapabilityFamily.DOC, True),
    "doc.get_children": _cap(CapabilityFamily.DOC, True),
    "doc.get_block": _cap(CapabilityFamily.DOC, True),
    "doc.update_block": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.delete_block": _cap(CapabilityFamily.DOC, False, "high"),
    "doc.move": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.get_database": _cap(CapabilityFamily.DOC, True),
    "doc.create_datasource": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.get_datasource": _cap(CapabilityFamily.DOC, True),
    "doc.update_datasource": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.list_templates": _cap(CapabilityFamily.DOC, True),
    "doc.get_self": _cap(CapabilityFamily.DOC, True),
    "doc.get_user": _cap(CapabilityFamily.DOC, True),
    "doc.get_users": _cap(CapabilityFamily.DOC, True),
```

---

### Task 3: Add new TOOL_TO_CAPABILITY entries + fix Playwright names

**Files:**
- Modify: `backend/src/integrations/capabilities.py`

- [ ] **Step 5: Add Filesystem tool mappings**

Add to TOOL_TO_CAPABILITY (new section):
```python
    # Filesystem MCP (@modelcontextprotocol/server-filesystem)
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
```

- [ ] **Step 6: Fix Playwright mappings + add new ones**

Remove these wrong/phantom entries:
```python
    "browser_wait": "browser.snapshot",    # WRONG NAME — real name is browser_wait_for
    "browser_pdf_save": "browser.screenshot",  # PHANTOM — doesn't exist in server
```

Add these new entries:
```python
    "browser_evaluate": "browser.execute",
    "browser_run_code": "browser.execute",
    "browser_install": "browser.install",
    "browser_navigate_back": "browser.navigate_back",
    "browser_take_screenshot": "browser.screenshot",
    "browser_wait_for": "browser.wait",
    "browser_fill_form": "browser.type",
```

- [ ] **Step 7: Add new Linear tool mappings**

Add to TOOL_TO_CAPABILITY (extend existing Linear section):
```python
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
```

- [ ] **Step 8: Add new Notion tool mappings**

Add to TOOL_TO_CAPABILITY (extend existing Notion MCP section):
```python
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
```

---

### Task 4: Fix agent scopes

**Files:**
- Modify: `backend/src/orchestrator/agents.py`

- [ ] **Step 9: Add filesystem + workflow.get_teams to Observer**

Add to Observer scope:
```python
        "filesystem.read",
        "filesystem.list",
        "filesystem.search",
        "workflow.get_teams",
```

Note: `workflow.get_teams` is already in Observer's scope — verify first. Only add if missing.

- [ ] **Step 10: Add calendar.delete + workflow.delete* to Operator**

Add to Operator scope:
```python
        "calendar.delete",
        "workflow.delete",
        "workflow.delete_comment",
        "workflow.delete_milestone",
```

- [ ] **Step 11: Add filesystem read to Researcher**

Add to Researcher scope:
```python
        "filesystem.read",
        "filesystem.list",
        "filesystem.search",
```

---

### Task 5: Run full suite + commit

- [ ] **Step 12: Run Phase 5 tests**

Run: `cd backend && python -m pytest tests/test_phase5_capabilities.py -v`

Expected: All tests PASS.

- [ ] **Step 13: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x -q`

Expected: All tests PASS.

- [ ] **Step 14: Commit**

```bash
cd backend
git add src/integrations/capabilities.py src/orchestrator/agents.py tests/test_phase5_capabilities.py
git commit -m "fix: add missing capabilities, tool mappings, and agent scope fixes

Add CapabilityFamily.FILESYSTEM with 6 capabilities. Add ~30 new
capabilities for browser, workflow, and doc families. Add ~55 new
TOOL_TO_CAPABILITY entries for Filesystem (14), Playwright (7),
Linear (17), and Notion (16) tools. Fix 3 wrong Playwright mappings.
Add filesystem/calendar.delete/workflow.delete to agent scopes.

Phase 5 of unified tool registry migration."
```

IMPORTANT: Only commit those 3 files.

---

## Exit Criteria

- [ ] `CapabilityFamily.FILESYSTEM` exists
- [ ] ~30 new capabilities in CAPABILITY_CATALOG
- [ ] All 14 Filesystem tools mapped in TOOL_TO_CAPABILITY
- [ ] All 22 Playwright tools mapped (7 new + 3 fixed)
- [ ] All 24 Linear tools mapped (17 new)
- [ ] All 22 Notion tools mapped (16 new)
- [ ] `browser_pdf_save` and `browser_wait` removed from TOOL_TO_CAPABILITY
- [ ] Observer has filesystem.read/list/search
- [ ] Operator has calendar.delete + workflow.delete/delete_comment/delete_milestone
- [ ] Researcher has filesystem.read/list/search
- [ ] All tests pass
