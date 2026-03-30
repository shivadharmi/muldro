# Phase 4: Fix External MCP Seed Bugs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 3 broken MCP server seeds (Google Workspace, Slack, Notion wrong names) and 2 incorrect tool name aliases (Linear) so that external tool dispatch uses correct real MCP names.

**Architecture:** Find-and-replace across 3 files. No structural changes.

**Tech Stack:** Python, pytest

---

## Changes Summary

| Fix | File | What |
|-----|------|------|
| Google Workspace executable | `seed_installations.py` | `google-workspace-mcp` → `google-workspace-worker` |
| Slack env var | `seed_installations.py` | `SLACK_BOT_TOKEN` → `SLACK_MCP_XOXP_TOKEN` + `SLACK_MCP_XOXB_TOKEN` |
| Notion tool names (6 entries) | `tool_registry.py` | Add `API-` prefix to all 6 kebab-case names |
| Notion capability mappings (6 entries) | `capabilities.py` | Add `API-` prefix to all 6 kebab-case names |
| Linear wrong aliases (2 entries) | `tool_registry.py` | `linear_comment` → duplicate of `linear_create_comment`, `linear_list_issues` → duplicate of `linear_search_issues` |
| Linear wrong capability mappings (2 entries) | `capabilities.py` | `linear_comment` → `linear_create_comment`, `linear_list_issues` → `linear_search_issues` |

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `backend/src/integrations/seed_installations.py` | Modify | Fix Google Workspace command + Slack env var |
| `backend/src/services/tool_registry.py` | Modify | Fix 6 Notion names + 2 Linear names in `_DEFAULT_TOOLS` |
| `backend/src/integrations/capabilities.py` | Modify | Fix 6 Notion names + 2 Linear names in `TOOL_TO_CAPABILITY` |
| `backend/tests/test_seed_installations.py` | Create | New test verifying seed correctness |

---

### Task 1: Create seed installation tests (RED)

**Files:**
- Create: `backend/tests/test_seed_installations.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for MCP server seed installations — verifies known bugs are fixed."""

from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS


class TestSeedInstallations:
    def _get_seed(self, server_name: str) -> dict:
        return next(s for s in _DEFAULT_INSTALLATIONS if s["server_name"] == server_name)

    def test_google_workspace_executable(self):
        """Google Workspace seed must use google-workspace-worker, not google-workspace-mcp."""
        seed = self._get_seed("google-workspace")
        assert seed["args"] == ["google-workspace-worker"], (
            f"Wrong executable: {seed['args']} — should be ['google-workspace-worker']"
        )

    def test_slack_env_vars(self):
        """Slack seed must use SLACK_MCP_XOXP_TOKEN, not SLACK_BOT_TOKEN."""
        seed = self._get_seed("slack")
        env_keys = set(seed["env_template"].keys())
        assert "SLACK_BOT_TOKEN" not in env_keys, "SLACK_BOT_TOKEN is wrong — use SLACK_MCP_XOXP_TOKEN"
        assert "SLACK_MCP_XOXP_TOKEN" in env_keys, "Missing SLACK_MCP_XOXP_TOKEN"
        assert "SLACK_MCP_XOXB_TOKEN" in env_keys, "Missing SLACK_MCP_XOXB_TOKEN"


class TestNotionToolNames:
    """Notion MCP uses API-prefixed kebab-case names, not plain kebab-case."""

    def test_notion_tools_in_default_tools(self):
        """All 6 Notion MCP tools must have API- prefix in _DEFAULT_TOOLS."""
        from src.services.tool_registry import _DEFAULT_TOOLS

        notion_tools = [t for t in _DEFAULT_TOOLS if t.get("connector_type") == "notion"]
        notion_names = {t["name"] for t in notion_tools}
        # Real names from live probe
        expected_api_names = {
            "API-post-page",
            "API-patch-page",
            "API-retrieve-a-page",
            "API-query-data-source",
            "API-create-a-comment",
            "API-patch-block-children",
        }
        for name in expected_api_names:
            assert name in notion_names, f"Missing Notion tool: {name}"
        # Old wrong names should be gone
        wrong_names = {"create-a-page", "update-a-page", "retrieve-a-page", "query-data-source",
                       "create-a-comment", "append-block-children"}
        present_wrong = wrong_names & notion_names
        assert not present_wrong, f"Wrong Notion names still present: {present_wrong}"

    def test_notion_capability_mappings(self):
        """Notion tools must use API- prefixed names in TOOL_TO_CAPABILITY."""
        from src.integrations.capabilities import TOOL_TO_CAPABILITY

        expected = {
            "API-post-page": "doc.create",
            "API-patch-page": "doc.update",
            "API-retrieve-a-page": "doc.get",
            "API-query-data-source": "doc.query",
            "API-create-a-comment": "doc.comment",
            "API-patch-block-children": "doc.append",
        }
        for tool_name, expected_cap in expected.items():
            assert TOOL_TO_CAPABILITY.get(tool_name) == expected_cap, (
                f"{tool_name} should map to {expected_cap}, "
                f"got {TOOL_TO_CAPABILITY.get(tool_name)}"
            )
        # Old wrong names should be gone
        for old in ("create-a-page", "update-a-page", "retrieve-a-page",
                     "query-data-source", "create-a-comment", "append-block-children"):
            assert old not in TOOL_TO_CAPABILITY, f"Old name '{old}' still in TOOL_TO_CAPABILITY"


class TestLinearToolNames:
    """Linear MCP uses linear_create_comment and linear_search_issues, not aliases."""

    def test_linear_no_wrong_aliases_in_defaults(self):
        """linear_comment and linear_list_issues are wrong names — should not exist."""
        from src.services.tool_registry import _DEFAULT_TOOLS

        names = {t["name"] for t in _DEFAULT_TOOLS}
        assert "linear_comment" not in names, "linear_comment is wrong — use linear_create_comment"
        assert "linear_list_issues" not in names, "linear_list_issues is wrong — use linear_search_issues"

    def test_linear_no_wrong_aliases_in_capabilities(self):
        """linear_comment and linear_list_issues should not be in TOOL_TO_CAPABILITY."""
        from src.integrations.capabilities import TOOL_TO_CAPABILITY

        assert "linear_comment" not in TOOL_TO_CAPABILITY, (
            "linear_comment is wrong — use linear_create_comment"
        )
        assert "linear_list_issues" not in TOOL_TO_CAPABILITY, (
            "linear_list_issues is wrong — use linear_search_issues"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_seed_installations.py -v`

Expected: Multiple failures (wrong executable, wrong env var, wrong Notion names, wrong Linear names).

---

### Task 2: Fix seed_installations.py

**Files:**
- Modify: `backend/src/integrations/seed_installations.py`

- [ ] **Step 3: Fix Google Workspace executable**

Change line 25:
```python
# BEFORE:
        "args": ["google-workspace-mcp"],
# AFTER:
        "args": ["google-workspace-worker"],
```

- [ ] **Step 4: Fix Slack env var**

Change line 82:
```python
# BEFORE:
        "env_template": {
            "SLACK_BOT_TOKEN": "Slack bot OAuth token",
        },
# AFTER:
        "env_template": {
            "SLACK_MCP_XOXP_TOKEN": "Slack user OAuth token (xoxp-...)",
            "SLACK_MCP_XOXB_TOKEN": "Slack bot OAuth token (xoxb-...)",
        },
```

- [ ] **Step 5: Run seed tests**

Run: `cd backend && python -m pytest tests/test_seed_installations.py::TestSeedInstallations -v`

Expected: Both seed tests PASS.

---

### Task 3: Fix Notion and Linear names in tool_registry.py

**Files:**
- Modify: `backend/src/services/tool_registry.py`

- [ ] **Step 6: Fix 6 Notion entries in _DEFAULT_TOOLS**

Replace the Notion MCP section (lines 186-191):
```python
# BEFORE:
    _t("create-a-page", "medium", True, "notion"),
    _t("update-a-page", "medium", True, "notion"),
    _t("retrieve-a-page", "low", False, "notion"),
    _t("query-data-source", "low", False, "notion"),
    _t("create-a-comment", "medium", True, "notion"),
    _t("append-block-children", "medium", True, "notion"),

# AFTER:
    _t("API-post-page", "medium", True, "notion"),
    _t("API-patch-page", "medium", True, "notion"),
    _t("API-retrieve-a-page", "low", False, "notion"),
    _t("API-query-data-source", "low", False, "notion"),
    _t("API-create-a-comment", "medium", True, "notion"),
    _t("API-patch-block-children", "medium", True, "notion"),
```

- [ ] **Step 7: Remove 2 wrong Linear aliases from _DEFAULT_TOOLS**

Remove lines 171-172 (the wrong aliases — the correct names already exist at lines 176-178):
```python
# DELETE these 2 lines:
    _t("linear_comment", "medium", True, "linear"),
    _t("linear_list_issues", "low", False, "linear"),
```

Note: `linear_create_comment` (line 176) and `linear_search_issues` (line 178) already exist with the correct real MCP names. The deleted entries are duplicates with wrong names.

---

### Task 4: Fix Notion and Linear names in capabilities.py

**Files:**
- Modify: `backend/src/integrations/capabilities.py`

- [ ] **Step 8: Fix 6 Notion entries in TOOL_TO_CAPABILITY**

Replace lines 264-269:
```python
# BEFORE:
    "create-a-page": "doc.create",
    "update-a-page": "doc.update",
    "retrieve-a-page": "doc.get",
    "query-data-source": "doc.query",
    "create-a-comment": "doc.comment",
    "append-block-children": "doc.append",

# AFTER:
    "API-post-page": "doc.create",
    "API-patch-page": "doc.update",
    "API-retrieve-a-page": "doc.get",
    "API-query-data-source": "doc.query",
    "API-create-a-comment": "doc.comment",
    "API-patch-block-children": "doc.append",
```

- [ ] **Step 9: Remove 2 wrong Linear entries from TOOL_TO_CAPABILITY**

Remove lines 249-250:
```python
# DELETE these 2 lines:
    "linear_comment": "workflow.comment",
    "linear_list_issues": "workflow.list",
```

Note: `linear_create_comment` and `linear_search_issues` should already have entries in TOOL_TO_CAPABILITY. Verify they exist; if not, add them.

- [ ] **Step 10: Run all Phase 4 tests**

Run: `cd backend && python -m pytest tests/test_seed_installations.py tests/test_capabilities.py tests/test_tool_registry.py -v`

Expected: All tests PASS.

---

### Task 5: Full suite + commit

- [ ] **Step 11: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x -q`

Expected: All tests PASS.

- [ ] **Step 12: Commit**

```bash
cd backend
git add src/integrations/seed_installations.py src/services/tool_registry.py src/integrations/capabilities.py tests/test_seed_installations.py
git commit -m "fix: correct external MCP seed bugs (Google Workspace, Slack, Notion, Linear)

Fix Google Workspace executable (google-workspace-mcp -> google-workspace-worker).
Fix Slack env var (SLACK_BOT_TOKEN -> SLACK_MCP_XOXP_TOKEN/SLACK_MCP_XOXB_TOKEN).
Fix 6 Notion tool names (add API- prefix to match real MCP names).
Remove 2 wrong Linear aliases (linear_comment, linear_list_issues).

Phase 4 of unified tool registry migration."
```

IMPORTANT: Only commit the 4 files listed. Do NOT commit other modified files.

---

## Exit Criteria

- [ ] Google Workspace seed uses `google-workspace-worker` executable
- [ ] Slack seed uses `SLACK_MCP_XOXP_TOKEN` and `SLACK_MCP_XOXB_TOKEN`
- [ ] All 6 Notion entries in `_DEFAULT_TOOLS` use `API-` prefix
- [ ] All 6 Notion entries in `TOOL_TO_CAPABILITY` use `API-` prefix
- [ ] No `linear_comment` or `linear_list_issues` in `_DEFAULT_TOOLS` or `TOOL_TO_CAPABILITY`
- [ ] All tests pass
