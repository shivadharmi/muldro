# Phase 2: Add Missing get_goal_memories Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `get_goal_memories` MCP tool visible to Claude — it already exists in the intelligence server but has no Pydantic schema, no `internal_tools` entry, no capability mapping.

**Architecture:** Add one Pydantic model, register it in 4 places, update tests. The MCP tool implementation already exists at `tools/intelligence_server.py:682`. Its parameters are `user_id` (injected at dispatch, excluded from schema), `limit` (default 10), and `workspace_id` (injected at dispatch, excluded from schema).

**Tech Stack:** Python, Pydantic, pytest

---

## Key Details

**MCP tool signature** (already implemented in intelligence_server.py:678-726):
```python
@intelligence.tool(tags={"planner", "read"}, annotations=ToolAnnotations(readOnlyHint=True))
async def get_goal_memories(user_id: str, ctx: Context, limit: int = 10, workspace_id: str = "") -> dict:
    """Get active user goals stored as memories.
    Goals are stored as memories with memory_type='goal' and scope='planning'.
    Returns goal text, confidence, and entity links.
    """
```

**Schema note:** `user_id` and `workspace_id` are excluded from the Pydantic model (Claude doesn't choose which user to act as — these are injected by `_call_internal_tool()`). Only `limit` is exposed.

**Capability note:** Phase 1 removed `internal.get_goals` from `CAPABILITY_CATALOG` (it was an orphan for the deleted `get_goals` tool). Phase 2 re-adds it for the real `get_goal_memories` tool. The Planner agent needs this capability in scope since the MCP tool has `tags={"planner", "read"}`.

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `backend/src/orchestrator/tool_schemas.py` | Modify | Add `GetGoalMemoriesInput` model + `TOOL_INPUT_MODELS` entry |
| `backend/src/orchestrator/jarvis.py` | Modify | Add `get_goal_memories` to `internal_tools` set |
| `backend/src/integrations/capabilities.py` | Modify | Re-add `internal.get_goals` to `CAPABILITY_CATALOG` + add `get_goal_memories` to `TOOL_TO_CAPABILITY` |
| `backend/src/orchestrator/agents.py` | Modify | Add `internal.get_goals` to Planner scope |
| `backend/tests/test_tool_schemas.py` | Modify | Update expected count 15→16, add `get_goal_memories` to expected set |

---

### Task 1: Update tests for new tool count (RED)

**Files:**
- Modify: `backend/tests/test_tool_schemas.py`

- [ ] **Step 1: Update test expectations**

Update `test_tool_count_is_15` to `test_tool_count_is_16` and add `get_goal_memories` to the expected set:

```python
    def test_tool_count_is_16(self):
        """15 original tools + get_goal_memories = 16 tools."""
        assert len(TOOL_INPUT_MODELS) == 16, (
            f"Expected 16 tools, got {len(TOOL_INPUT_MODELS)}: {sorted(TOOL_INPUT_MODELS.keys())}"
        )
```

In `test_expected_tools_present`, add `"get_goal_memories"` to the expected set (16 tools total).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tool_schemas.py -v`

Expected: `test_tool_count_is_16` FAILS (count is 15), `test_expected_tools_present` FAILS (missing get_goal_memories)

---

### Task 2: Add GetGoalMemoriesInput Pydantic model and register

**Files:**
- Modify: `backend/src/orchestrator/tool_schemas.py`

- [ ] **Step 3: Add the Pydantic model class**

Add after `ExtractPreferencesInput` (line 116), before `BuildContextInput`:

```python
class GetGoalMemoriesInput(BaseModel):
    """Get active user goals stored as memories.

    Goals are stored as memories with memory_type='goal' and scope='planning'.
    Returns goal text, confidence, and entity links.
    """

    limit: int = Field(default=10, ge=1, le=50, description="Maximum goals to return")
```

- [ ] **Step 4: Add to TOOL_INPUT_MODELS dict**

Add this entry to `TOOL_INPUT_MODELS`:

```python
    "get_goal_memories": GetGoalMemoriesInput,
```

- [ ] **Step 5: Run tool schema tests**

Run: `cd backend && python -m pytest tests/test_tool_schemas.py -v`

Expected: All 6 tests PASS (GREEN).

---

### Task 3: Add to internal_tools set and capability mappings

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (~line 2516)
- Modify: `backend/src/integrations/capabilities.py`
- Modify: `backend/src/orchestrator/agents.py`

- [ ] **Step 6: Add to internal_tools set in jarvis.py**

Add `"get_goal_memories"` to the `internal_tools` set (after `"extract_preferences"`):

```python
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
            "get_goal_memories",
            "build_context",
            "verify_run",
        }
```

Result: 15 entries (was 14).

- [ ] **Step 7: Re-add internal.get_goals to CAPABILITY_CATALOG**

In `capabilities.py`, add to the `CAPABILITY_CATALOG` dict in the internal section (near the other `internal.*` entries):

```python
    "internal.get_goals": _cap(CapabilityFamily.INTERNAL, True),
```

- [ ] **Step 8: Add get_goal_memories to TOOL_TO_CAPABILITY**

In `capabilities.py`, add to the `TOOL_TO_CAPABILITY` dict in the internal section:

```python
    "get_goal_memories": "internal.get_goals",
```

- [ ] **Step 9: Add internal.get_goals to Planner scope**

In `agents.py`, add `"internal.get_goals"` to the Planner's capability scope:

```python
    "planner": {
        "internal.get_plans",
        "internal.get_goals",
        "internal.search",
    },
```

- [ ] **Step 10: Run related tests**

Run: `cd backend && python -m pytest tests/test_tool_schemas.py tests/test_orchestrator.py tests/test_capabilities.py -v`

Expected: All tests PASS.

---

### Task 4: Run full test suite and commit

- [ ] **Step 11: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x -q`

Expected: All tests PASS.

- [ ] **Step 12: Verify end-to-end**

```bash
cd backend && python -c "
from src.orchestrator.tool_schemas import TOOL_INPUT_MODELS, build_tool_definitions
from src.integrations.capabilities import get_capability_for_tool
print(f'TOOL_INPUT_MODELS count: {len(TOOL_INPUT_MODELS)}')
assert len(TOOL_INPUT_MODELS) == 16
assert 'get_goal_memories' in TOOL_INPUT_MODELS
cap = get_capability_for_tool('get_goal_memories')
print(f'get_goal_memories capability: {cap}')
assert cap == 'internal.get_goals'
print('All assertions passed!')
"
```

- [ ] **Step 13: Commit**

```bash
cd backend
git add src/orchestrator/tool_schemas.py src/orchestrator/jarvis.py src/integrations/capabilities.py src/orchestrator/agents.py tests/test_tool_schemas.py
git commit -m "fix: add get_goal_memories tool schema and capability mapping

Make get_goal_memories visible to Claude — the MCP implementation existed
but had no Pydantic schema, no internal_tools entry, and no capability
mapping. Re-adds internal.get_goals capability (removed in Phase 1 as
orphan of the old get_goals tool) for the real implementation.

Phase 2 of unified tool registry migration."
```

---

## Exit Criteria Checklist

- [ ] `GetGoalMemoriesInput` Pydantic model exists in `tool_schemas.py`
- [ ] `TOOL_INPUT_MODELS` has exactly 16 entries
- [ ] `get_goal_memories` in `internal_tools` set in jarvis.py (15 entries total)
- [ ] `TOOL_TO_CAPABILITY` maps `get_goal_memories` → `internal.get_goals`
- [ ] `CAPABILITY_CATALOG` includes `internal.get_goals`
- [ ] Planner agent has `internal.get_goals` in capability scope
- [ ] All tests pass (including updated test_tool_schemas.py)
- [ ] `build_tool_definitions()` returns 16 definitions
