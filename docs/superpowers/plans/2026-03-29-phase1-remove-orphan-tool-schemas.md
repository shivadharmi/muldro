# Phase 1: Remove Orphan Tool Schemas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove 3 orphan tools (`create_task`, `get_task`, `get_goals`) that are presented to Claude but have no MCP implementation — calling them wastes Claude's tool budget and fails silently.

**Architecture:** Pure deletion. Remove the Pydantic models, their entries in `TOOL_INPUT_MODELS`, the `internal_tools` set, `TOOL_TO_CAPABILITY`, `CAPABILITY_CATALOG`, and `_DEFAULT_TOOLS`. No new code. The `create_task` **Planner decision type** (in contracts.py, route_resolver.py, and tests) is completely separate and must NOT be touched.

**Tech Stack:** Python, Pydantic, pytest

---

## Critical Distinction

`create_task` exists in two completely independent systems:

| System | What it is | Location | Action |
|--------|-----------|----------|--------|
| **MCP Tool** (orphan) | Pydantic model `CreateTaskInput` + dispatch entry | `tool_schemas.py`, `jarvis.py` `internal_tools`, `TOOL_TO_CAPABILITY`, `_DEFAULT_TOOLS` | **DELETE** |
| **Planner Decision** | Decision type in `PlannerOutput.decision` | `contracts.py`, `route_resolver.py`, `agents.py`, all tests using `decision="create_task"` | **DO NOT TOUCH** |

The MCP tool `create_task` was removed when standalone tasks were eliminated in the product redesign. The Planner decision `create_task` is alive and routes to `Governor → Operator` pipeline.

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `backend/src/orchestrator/tool_schemas.py` | Modify | Remove 3 Pydantic models + 3 `TOOL_INPUT_MODELS` entries |
| `backend/src/orchestrator/jarvis.py` | Modify | Remove 3 entries from `internal_tools` set (line ~2529-2531) |
| `backend/src/integrations/capabilities.py` | Modify | Remove 3 entries from `TOOL_TO_CAPABILITY` (lines 331, 332, 340) + 3 entries from `CAPABILITY_CATALOG` (lines 143, 144, 152) |
| `backend/src/services/tool_registry.py` | Modify | Remove 3 entries from `_DEFAULT_TOOLS` (lines 101, 102, 111) |
| `backend/tests/test_tool_schemas.py` | Create | New test file verifying orphans are gone and remaining tools are correct |

---

### Task 1: Write tests verifying current tool count and orphan absence

**Files:**
- Create: `backend/tests/test_tool_schemas.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for tool schema registry — verifies orphan tools are removed."""

from src.orchestrator.tool_schemas import TOOL_INPUT_MODELS, build_tool_definitions


class TestToolInputModels:
    def test_orphan_tools_not_present(self):
        """create_task, get_task, get_goals have no MCP implementation — must not be in registry."""
        orphans = {"create_task", "get_task", "get_goals"}
        present = orphans & set(TOOL_INPUT_MODELS.keys())
        assert present == set(), f"Orphan tools still in TOOL_INPUT_MODELS: {present}"

    def test_tool_count_is_15(self):
        """After removing 3 orphans from 18, exactly 15 tools should remain."""
        assert len(TOOL_INPUT_MODELS) == 15, (
            f"Expected 15 tools, got {len(TOOL_INPUT_MODELS)}: {sorted(TOOL_INPUT_MODELS.keys())}"
        )

    def test_all_models_have_docstrings(self):
        """Every tool model must have a docstring (used as Claude tool description)."""
        for name, model_cls in TOOL_INPUT_MODELS.items():
            assert model_cls.__doc__, f"Tool '{name}' model {model_cls.__name__} has no docstring"

    def test_build_tool_definitions_returns_correct_count(self):
        """build_tool_definitions() should return one definition per TOOL_INPUT_MODELS entry."""
        defs = build_tool_definitions()
        assert len(defs) == len(TOOL_INPUT_MODELS)

    def test_build_tool_definitions_structure(self):
        """Each tool definition must have name, description, and input_schema."""
        defs = build_tool_definitions()
        for tool_def in defs:
            assert "name" in tool_def, f"Missing 'name' in tool definition"
            assert "description" in tool_def, f"Missing 'description' for {tool_def.get('name')}"
            assert "input_schema" in tool_def, f"Missing 'input_schema' for {tool_def.get('name')}"
            assert tool_def["input_schema"]["type"] == "object", (
                f"input_schema for {tool_def['name']} must be type 'object'"
            )

    def test_expected_tools_present(self):
        """Verify the 15 expected internal tools are all present."""
        expected = {
            "ingest_event",
            "search",
            "evaluate_policy",
            "get_briefing",
            "get_observation_cursor",
            "update_observation_cursor",
            "report_observation",
            "approve_action",
            "update_execution",
            "update_entity",
            "get_active_plans",
            "extract_preferences",
            "build_context",
            "verify_run",
            "report_governor_verdict",
        }
        actual = set(TOOL_INPUT_MODELS.keys())
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"Missing expected tools: {missing}"
        assert not extra, f"Unexpected extra tools: {extra}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tool_schemas.py -v`

Expected failures:
- `test_orphan_tools_not_present` — FAIL (orphans still present)
- `test_tool_count_is_15` — FAIL (count is 18)
- `test_expected_tools_present` — FAIL (extra tools: create_task, get_task, get_goals)
- `test_all_models_have_docstrings` — PASS (all have docstrings)
- `test_build_tool_definitions_returns_correct_count` — PASS (18 == 18)
- `test_build_tool_definitions_structure` — PASS

---

### Task 2: Remove orphan Pydantic models and TOOL_INPUT_MODELS entries

**Files:**
- Modify: `backend/src/orchestrator/tool_schemas.py`

- [ ] **Step 3: Remove the 3 orphan Pydantic model classes**

Delete `CreateTaskInput` (lines 119–127), `GetTaskInput` (lines 129–133), and `GetGoalsInput` (lines 135–140) from `tool_schemas.py`:

```python
# DELETE this entire block (CreateTaskInput class):
class CreateTaskInput(BaseModel):
    """Create a standalone task in the task system."""

    title: str = Field(description="Task title")
    description: str = Field(default="", description="Detailed task description")
    task_type: str = Field(default="general", description="Task type: general, follow_up, research")
    priority: str = Field(default="medium", description="Priority: low, medium, high, critical")
    goal_id: str = Field(default="", description="Optional parent goal ID")


# DELETE this entire block (GetTaskInput class):
class GetTaskInput(BaseModel):
    """Get details of a task by ID."""

    task_id: str = Field(description="Task ID to retrieve")


# DELETE this entire block (GetGoalsInput class):
class GetGoalsInput(BaseModel):
    """Get user goals, optionally filtered by status."""

    status: str = Field(default="active", description="Filter by status: active, completed, all")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum goals to return")
```

- [ ] **Step 4: Remove the 3 entries from TOOL_INPUT_MODELS dict**

In the `TOOL_INPUT_MODELS` dict, remove these 3 lines:

```python
# DELETE these 3 lines from TOOL_INPUT_MODELS:
    "create_task": CreateTaskInput,
    "get_task": GetTaskInput,
    "get_goals": GetGoalsInput,
```

The remaining dict should have 15 entries:

```python
TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "ingest_event": IngestEventInput,
    "search": SearchInput,
    "evaluate_policy": EvaluatePolicyInput,
    "get_briefing": GetBriefingInput,
    "get_observation_cursor": GetObservationCursorInput,
    "update_observation_cursor": UpdateObservationCursorInput,
    "report_observation": ReportObservationInput,
    "approve_action": ApproveActionInput,
    "update_execution": UpdateExecutionInput,
    "update_entity": UpdateEntityInput,
    "get_active_plans": GetActivePlansInput,
    "extract_preferences": ExtractPreferencesInput,
    "build_context": BuildContextInput,
    "verify_run": VerifyRunInput,
    "report_governor_verdict": ReportGovernorVerdictInput,
}
```

- [ ] **Step 5: Run the tool schema tests**

Run: `cd backend && python -m pytest tests/test_tool_schemas.py -v`

Expected: All 6 tests PASS.

---

### Task 3: Remove orphans from internal_tools set in jarvis.py

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:2529-2531`

- [ ] **Step 6: Remove the 3 orphan entries from internal_tools set**

In `_execute_tool()` around line 2516, the `internal_tools` set has 17 entries. Remove `"create_task"`, `"get_task"`, `"get_goals"`:

```python
# BEFORE (17 entries):
        internal_tools = {
            "ingest_event",
            "search",
            "update_entity",
            "get_active_plans",
            "evaluate_policy",
            "approve_action",
            "get_briefing",
            "get_observation_cursor",
            "update_observation_cursor",
            "report_observation",
            "update_execution",
            "extract_preferences",
            "create_task",       # ← DELETE
            "get_task",          # ← DELETE
            "get_goals",         # ← DELETE
            "build_context",
            "verify_run",
        }

# AFTER (14 entries):
        internal_tools = {
            "ingest_event",
            "search",
            "update_entity",
            "get_active_plans",
            "evaluate_policy",
            "approve_action",
            "get_briefing",
            "get_observation_cursor",
            "update_observation_cursor",
            "report_observation",
            "update_execution",
            "extract_preferences",
            "build_context",
            "verify_run",
        }
```

Note: This set has 14 entries (not 15) because `report_governor_verdict` is handled separately as a special dispatch case earlier in `_execute_tool()`, not via the `internal_tools` set.

- [ ] **Step 7: Run existing orchestrator tests to verify no regressions**

Run: `cd backend && python -m pytest tests/test_orchestrator.py -v`

Expected: All tests PASS. The `create_task` references in these tests are Planner **decision types**, not MCP tools.

---

### Task 4: Remove orphans from capabilities.py

**Files:**
- Modify: `backend/src/integrations/capabilities.py:143-144,152,331-332,340`

- [ ] **Step 8: Remove 3 entries from TOOL_TO_CAPABILITY**

Remove these 3 lines from the `TOOL_TO_CAPABILITY` dict:

```python
# DELETE these 3 lines:
    "get_task": "internal.get_task",
    "get_goals": "internal.get_goals",
    "create_task": "internal.create_task",
```

- [ ] **Step 9: Remove 3 entries from CAPABILITY_CATALOG**

Remove these 3 lines from the `CAPABILITY_CATALOG` dict:

```python
# DELETE these 3 lines:
    "internal.get_task": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_goals": _cap(CapabilityFamily.INTERNAL, True),
    "internal.create_task": _cap(CapabilityFamily.INTERNAL, False),
```

- [ ] **Step 10: Run capabilities tests**

Run: `cd backend && python -m pytest tests/test_capabilities.py -v`

Expected: All tests PASS. No test references these 3 orphan capabilities.

---

### Task 5: Remove orphans from _DEFAULT_TOOLS in tool_registry.py

**Files:**
- Modify: `backend/src/services/tool_registry.py:101-102,111`

- [ ] **Step 11: Remove 3 entries from _DEFAULT_TOOLS**

Remove these 3 lines from `_DEFAULT_TOOLS`:

```python
# DELETE these 3 lines:
    _t("get_task", "low", False, "internal"),
    _t("get_goals", "low", False, "internal"),
    _t("create_task", "low", False, "internal"),
```

- [ ] **Step 12: Run tool registry tests**

Run: `cd backend && python -m pytest tests/test_tool_registry.py -v`

Expected: All tests PASS.

---

### Task 6: Run full test suite and commit

- [ ] **Step 13: Run the full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x`

Expected: All tests PASS. No test depends on the orphan tools.

Key tests to watch:
- `test_orchestrator.py` — uses `create_task` as a **decision** type (not a tool) → should pass
- `test_route_resolver.py` — uses `create_task` as a **decision** type → should pass
- `test_perception_execution.py` — uses `create_task` as a **decision** type → should pass
- `test_tool_schemas.py` — our new tests → should pass
- `test_capabilities.py` — should pass (orphan capabilities removed)
- `test_tool_registry.py` — should pass (orphan defaults removed)

- [ ] **Step 14: Verify the orphan count reduction**

Run a quick sanity check:

```bash
cd backend && python -c "
from src.orchestrator.tool_schemas import TOOL_INPUT_MODELS, build_tool_definitions
print(f'TOOL_INPUT_MODELS count: {len(TOOL_INPUT_MODELS)}')
print(f'build_tool_definitions count: {len(build_tool_definitions())}')
print(f'Tools: {sorted(TOOL_INPUT_MODELS.keys())}')
assert len(TOOL_INPUT_MODELS) == 15, f'Expected 15, got {len(TOOL_INPUT_MODELS)}'
assert 'create_task' not in TOOL_INPUT_MODELS
assert 'get_task' not in TOOL_INPUT_MODELS
assert 'get_goals' not in TOOL_INPUT_MODELS
print('All assertions passed!')
"
```

Expected output:
```
TOOL_INPUT_MODELS count: 15
build_tool_definitions count: 15
Tools: ['approve_action', 'build_context', 'evaluate_policy', 'extract_preferences', 'get_active_plans', 'get_briefing', 'get_observation_cursor', 'ingest_event', 'report_governor_verdict', 'report_observation', 'search', 'update_entity', 'update_execution', 'update_observation_cursor', 'verify_run']
All assertions passed!
```

- [ ] **Step 15: Commit**

```bash
cd backend
git add src/orchestrator/tool_schemas.py src/orchestrator/jarvis.py src/integrations/capabilities.py src/services/tool_registry.py tests/test_tool_schemas.py
git commit -m "fix: remove orphan tool schemas (create_task, get_task, get_goals)

Remove 3 tools from Claude's tool list that had no MCP implementation.
These were leftover from the standalone tasks/goals product redesign.
Calling them would fail with an MCP error and waste tool budget.

Note: the 'create_task' Planner decision type is unaffected — it routes
to Governor → Operator and is fully functional.

Phase 1 of unified tool registry migration."
```

---

## Exit Criteria Checklist

After all tasks complete:

- [ ] `TOOL_INPUT_MODELS` has exactly 15 entries
- [ ] `build_tool_definitions()` returns 15 tool definitions
- [ ] `internal_tools` set in jarvis.py has 14 entries (report_governor_verdict handled separately)
- [ ] `TOOL_TO_CAPABILITY` has no entries for `create_task`, `get_task`, `get_goals`
- [ ] `CAPABILITY_CATALOG` has no entries for `internal.create_task`, `internal.get_task`, `internal.get_goals`
- [ ] `_DEFAULT_TOOLS` has no entries for `create_task`, `get_task`, `get_goals`
- [ ] All existing tests pass unchanged
- [ ] 6 new tests in `test_tool_schemas.py` all pass
- [ ] No `CreateTaskInput`, `GetTaskInput`, or `GetGoalsInput` classes exist in codebase
