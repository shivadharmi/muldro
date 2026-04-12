# Agent Self-Sufficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all capability gaps so every agent can autonomously fulfill the routes it serves, without hardcoded handlers masking missing tools.

**Architecture:** Add read capabilities to the Operator (so it can read before writing), add memory/goal write tools for Librarian/Planner/Persona (so they can act, not just advise), handle the `ignore` decision properly, and expand the Planner decision framework documentation.

**Tech Stack:** Python, FastMCP, Pydantic, pytest, SQLAlchemy

---

## File Map

| File | Responsibility | Tasks |
|------|---------------|-------|
| `backend/src/orchestrator/agents.py` | Agent capability scopes | 1, 3 |
| `backend/src/tools/schemas.py` | Pydantic input models for internal tools | 2 |
| `backend/src/tools/catalog.py` | Internal tool definitions | 2 |
| `backend/src/tools/intelligence_server.py` | Internal MCP tool implementations | 2 |
| `backend/src/integrations/capabilities.py` | Capability catalog | 2 |
| `backend/src/orchestrator/jarvis.py` | Orchestrator — `ignore` handling | 4 |
| `backend/src/orchestrator/prompts.py` | Agent prompts, decision framework | 5 |
| `backend/src/services/route_resolver.py` | Route definitions | 4 |
| `backend/tests/test_tool_normalization.py` | Capability scope tests | 1, 3 |
| `backend/tests/test_route_resolver.py` | Route tests | 4 |
| `backend/tests/test_store_memory_tool.py` | New tool tests | 2 |
| `backend/tests/test_ignore_decision.py` | Ignore handling tests | 4 |

---

### Task 1: Operator Read-Before-Write Capabilities

**Files:**
- Modify: `backend/src/orchestrator/agents.py:73-107`
- Modify: `backend/tests/test_tool_normalization.py:14-25`

The Operator can write to calendar, messaging, issues, repos, and docs — but can't read them first. Every write domain needs its corresponding read.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_tool_normalization.py` inside `test_operator_capability_scope_has_expected_capabilities`:

```python
    def test_operator_capability_scope_has_expected_capabilities(self):
        """Operator scope should have read + write capabilities for autonomous tool use."""
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["operator"]

        # Write capabilities
        assert "email.send" in scope
        assert "email.draft" in scope
        assert "calendar.create" in scope
        assert "messaging.send" in scope
        assert "issue.create" in scope
        assert "doc.create" in scope

        # Read capabilities (needed for context gathering before writes)
        assert "email.read" in scope
        assert "email.list" in scope
        assert "email.search" in scope

        # Calendar read (needed before update/delete)
        assert "calendar.list" in scope
        assert "calendar.get" in scope

        # Messaging read (needed before reply)
        assert "messaging.list_channels" in scope
        assert "messaging.get_history" in scope
        assert "messaging.get_thread" in scope

        # Issue read (needed before update/comment)
        assert "issue.list" in scope
        assert "issue.get" in scope
        assert "issue.search" in scope

        # Repo read (needed before merge/update PR)
        assert "repo.list_prs" in scope
        assert "repo.get_diff" in scope
        assert "repo.get_reviews" in scope
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tool_normalization.py::TestAgentScopeDeduplication::test_operator_capability_scope_has_expected_capabilities -v`

Expected: FAIL — `calendar.list`, `messaging.list_channels`, `issue.list`, `repo.list_prs`, `repo.get_diff`, `repo.get_reviews` not in scope.

- [ ] **Step 3: Add missing read capabilities to Operator scope**

In `backend/src/orchestrator/agents.py`, update the `"operator"` entry in `AGENT_CAPABILITY_SCOPES`:

```python
    "operator": {
        # Email (read + write)
        "email.list",
        "email.read",
        "email.search",
        "email.send",
        "email.draft",
        "email.reply",
        # Calendar (read + write)
        "calendar.list",
        "calendar.get",
        "calendar.create",
        "calendar.update",
        "calendar.delete",
        # Messaging (read + write)
        "messaging.list_channels",
        "messaging.get_history",
        "messaging.get_thread",
        "messaging.send",
        "messaging.reply",
        "messaging.react",
        "messaging.update",
        "messaging.send_template",
        "messaging.post",
        "messaging.share",
        # Issues (read + write)
        "issue.list",
        "issue.get",
        "issue.search",
        "issue.create",
        "issue.update",
        "issue.comment",
        "issue.transition",
        "issue.sub_issue",
        # Repos (read + write)
        "repo.list_prs",
        "repo.get_diff",
        "repo.get_reviews",
        "repo.create_pr",
        "repo.merge_pr",
        "repo.update_pr",
        # Workflow (read + write)
        "workflow.list",
        "workflow.get",
        "workflow.search",
        "workflow.create_issue",
        "workflow.update_issue",
        "workflow.transition",
        "workflow.comment",
        "workflow.delete",
        "workflow.delete_comment",
        "workflow.delete_milestone",
        # Docs (keep existing write-only — doc reads are less common for Operator)
        "doc.create",
        "doc.update",
        "doc.comment",
        "doc.append",
        # Internal
        "internal.update_execution",
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_tool_normalization.py -v`

Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd backend && python -m pytest tests/ --ignore=tests/e2e -v 2>&1 | tail -5`

Expected: All tests pass. Watch for `test_assemble_context` or capability validation tests.

- [ ] **Step 6: Lint**

Run: `cd backend && ruff check src/orchestrator/agents.py tests/test_tool_normalization.py`

Expected: All checks passed.

- [ ] **Step 7: Commit**

```bash
git add backend/src/orchestrator/agents.py backend/tests/test_tool_normalization.py
git commit -m "feat: add read capabilities to Operator for autonomous read-before-write"
```

---

### Task 2: Internal MCP Tools for Librarian and Persona

**Files:**
- Create: `backend/tests/test_store_memory_tool.py`
- Modify: `backend/src/tools/schemas.py`
- Modify: `backend/src/tools/catalog.py`
- Modify: `backend/src/tools/intelligence_server.py`
- Modify: `backend/src/integrations/capabilities.py`
- Modify: `backend/src/orchestrator/agents.py`

The Librarian needs a `store_memory` tool to write memories (for `remember` and `add_to_brief` routes). The Persona needs a `store_preference` tool to store extracted preferences. Both currently rely on direct handlers in the orchestrator that bypass the agent entirely.

We add two new internal MCP tools:
- `store_memory` — general memory storage (scope, type, TTL configurable)
- `store_preference` — stores preference memories from Persona extractions

- [ ] **Step 1: Write failing tests for the new tools**

Create `backend/tests/test_store_memory_tool.py`:

```python
"""Tests for store_memory and store_preference internal MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestStoreMemorySchema:
    """Test that store_memory tool is registered in catalog."""

    def test_store_memory_in_catalog(self):
        from src.tools.catalog import INTERNAL_TOOLS

        names = {t.name for t in INTERNAL_TOOLS}
        assert "store_memory" in names

    def test_store_memory_capability(self):
        from src.tools.catalog import INTERNAL_TOOLS

        tool = next(t for t in INTERNAL_TOOLS if t.name == "store_memory")
        assert tool.capability == "internal.store_memory"
        assert tool.read_only is False
        assert tool.server == "intelligence"

    def test_store_preference_in_catalog(self):
        from src.tools.catalog import INTERNAL_TOOLS

        names = {t.name for t in INTERNAL_TOOLS}
        assert "store_preference" in names

    def test_store_preference_capability(self):
        from src.tools.catalog import INTERNAL_TOOLS

        tool = next(t for t in INTERNAL_TOOLS if t.name == "store_preference")
        assert tool.capability == "internal.store_preference"
        assert tool.read_only is False


class TestLibrarianHasStoreMemory:
    """Librarian agent must have store_memory capability."""

    def test_librarian_has_store_memory(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["librarian"]
        assert "internal.store_memory" in scope

    def test_librarian_has_update_entity(self):
        """Existing capability should not be removed."""
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["librarian"]
        assert "internal.update_entity" in scope
        assert "internal.search" in scope


class TestPersonaHasStorePreference:
    """Persona agent must have store_preference capability."""

    def test_persona_has_store_preference(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["persona"]
        assert "internal.store_preference" in scope

    def test_persona_retains_existing(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["persona"]
        assert "internal.search" in scope
        assert "internal.extract_preferences" in scope


class TestCapabilityCatalog:
    """New capabilities must be in CAPABILITY_CATALOG."""

    def test_store_memory_capability_exists(self):
        from src.integrations.capabilities import CAPABILITY_CATALOG

        assert "internal.store_memory" in CAPABILITY_CATALOG

    def test_store_preference_capability_exists(self):
        from src.integrations.capabilities import CAPABILITY_CATALOG

        assert "internal.store_preference" in CAPABILITY_CATALOG
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_store_memory_tool.py -v`

Expected: FAIL — `store_memory` not in catalog, capabilities not defined.

- [ ] **Step 3: Add Pydantic input models**

Add to `backend/src/tools/schemas.py` before the `# ── Registry ──` section:

```python
class StoreMemoryInput(BaseModel):
    """Store a memory in the knowledge base.

    Memories are typed (fact, goal, preference, briefing_item, task_context)
    and scoped (general, planning, personal). TTL controls retention.
    """

    text: str = Field(description="Memory content text")
    memory_type: str = Field(
        default="fact",
        description="Memory type: fact, goal, preference, briefing_item, task_context",
    )
    scope: str = Field(
        default="general",
        description="Memory scope: general, planning, personal",
    )
    ttl_days: int = Field(
        default=0,
        ge=0,
        description="Time-to-live in days. 0 = no expiry.",
    )
    entity_ids: str = Field(
        default="",
        description="Comma-separated entity IDs to link to this memory",
    )
    source: str = Field(
        default="agent",
        description="Origin of this memory: agent, perception, user",
    )


class StorePreferenceInput(BaseModel):
    """Store a user preference extracted from interactions.

    Preferences are memories with memory_type='preference' and long TTL.
    Used by Persona agent after extracting preference signals.
    """

    text: str = Field(description="Preference description (e.g., 'Prefers morning meetings')")
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence in this preference (0.0-1.0)",
    )
    source_text: str = Field(
        default="",
        description="Original text the preference was extracted from",
    )
```

Also add to the `TOOL_INPUT_MODELS` dict:

```python
    "store_memory": StoreMemoryInput,
    "store_preference": StorePreferenceInput,
```

- [ ] **Step 4: Add capabilities to CAPABILITY_CATALOG**

Add to `backend/src/integrations/capabilities.py` in the `CAPABILITY_CATALOG` dict, under the `# Internal` section:

```python
    "internal.store_memory": _cap(CapabilityFamily.INTERNAL, False),
    "internal.store_preference": _cap(CapabilityFamily.INTERNAL, False),
```

- [ ] **Step 5: Add tool definitions to catalog**

Add to `backend/src/tools/catalog.py` in the `INTERNAL_TOOLS` list. First add the new imports to the import block:

```python
from src.tools.schemas import (
    # ... existing imports ...
    StoreMemoryInput,
    StorePreferenceInput,
)
```

Then add the tool definitions (after the existing `verify_run` entry):

```python
    InternalToolDef(
        name="store_memory",
        input_model=StoreMemoryInput,
        capability="internal.store_memory",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(StoreMemoryInput),
        read_only=False,
    ),
    InternalToolDef(
        name="store_preference",
        input_model=StorePreferenceInput,
        capability="internal.store_preference",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(StorePreferenceInput),
        read_only=False,
    ),
```

- [ ] **Step 6: Implement MCP tool functions**

Add to `backend/src/tools/intelligence_server.py` (after the existing `verify_run` tool):

```python
# ── Memory Storage ───────────────────────────────────────────────────


@intelligence.tool(
    tags={"librarian", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def store_memory(
    user_id: str,
    text: str,
    ctx: Context,
    memory_type: str = "fact",
    scope: str = "general",
    ttl_days: int = 0,
    entity_ids: str = "",
    source: str = "agent",
    workspace_id: str = "",
) -> dict:
    """Store a memory in the knowledge base.

    Memories are typed (fact, goal, preference, briefing_item, task_context)
    and scoped (general, planning, personal). TTL controls retention.
    """
    async with _get_db() as db:
        try:
            memory_svc = _services.memory_service
            if not memory_svc:
                return make_error_response(RuntimeError("Memory service not available"))

            linked_ids = [e.strip() for e in entity_ids.split(",") if e.strip()] if entity_ids else None

            if memory_type == "goal":
                mid = await memory_svc.store_goal_memory(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    title=text,
                    entity_ids=linked_ids,
                )
            elif memory_type == "briefing_item":
                mid = await memory_svc.store_briefing_memory(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    text=text,
                    source=source,
                )
            else:
                mid = await memory_svc.store_memory(
                    user_id=user_id,
                    fact_text=text,
                    memory_type=memory_type,
                    scope=scope,
                    entity_ids=linked_ids or [],
                    workspace_id=workspace_id,
                    ttl_days=ttl_days if ttl_days > 0 else None,
                )
            await db.commit()
            await ctx.info(f"Stored {memory_type} memory: {text[:80]}")
            return {"status": "stored", "memory_id": mid}
        except Exception as e:
            logger.error("store_memory failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.tool(
    tags={"persona", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def store_preference(
    user_id: str,
    text: str,
    ctx: Context,
    confidence: float = 0.5,
    source_text: str = "",
    workspace_id: str = "",
) -> dict:
    """Store a user preference extracted from interactions.

    Preferences are memories with memory_type='preference' and long TTL.
    """
    async with _get_db() as db:
        try:
            memory_svc = _services.memory_service
            if not memory_svc:
                return make_error_response(RuntimeError("Memory service not available"))

            mid = await memory_svc.store_instruction_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                instruction_text=text,
                instruction_type="preference",
            )
            await db.commit()
            await ctx.info(f"Stored preference: {text[:80]} (confidence={confidence})")
            return {"status": "stored", "memory_id": mid, "confidence": confidence}
        except Exception as e:
            logger.error("store_preference failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)
```

- [ ] **Step 7: Add capabilities to agent scopes**

In `backend/src/orchestrator/agents.py`, update:

```python
    "librarian": {
        "internal.update_entity",
        "internal.search",
        "internal.store_memory",
    },
```

```python
    "persona": {
        "internal.search",
        "internal.extract_preferences",
        "internal.store_preference",
    },
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_store_memory_tool.py -v`

Expected: All PASS.

- [ ] **Step 9: Run full test suite**

Run: `cd backend && python -m pytest tests/ --ignore=tests/e2e -v 2>&1 | tail -5`

Expected: All tests pass. Watch for `test_validation.py` (validates tool catalog integrity) and `test_agent_registry.py` (validates capability scopes match).

- [ ] **Step 10: Lint**

Run: `cd backend && ruff check src/tools/schemas.py src/tools/catalog.py src/tools/intelligence_server.py src/integrations/capabilities.py src/orchestrator/agents.py tests/test_store_memory_tool.py`

Expected: All checks passed.

- [ ] **Step 11: Commit**

```bash
git add backend/src/tools/schemas.py backend/src/tools/catalog.py \
  backend/src/tools/intelligence_server.py backend/src/integrations/capabilities.py \
  backend/src/orchestrator/agents.py backend/tests/test_store_memory_tool.py
git commit -m "feat: add store_memory and store_preference internal MCP tools"
```

---

### Task 3: Planner Goal Write Capability

**Files:**
- Modify: `backend/src/orchestrator/agents.py:64-68`
- Modify: `backend/tests/test_tool_normalization.py` (or add test in `test_store_memory_tool.py`)

The Planner serves the `goal_update` route but has no write tools. The `store_memory` tool from Task 2 (with `memory_type="goal"`) handles this — we just need to give Planner access.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_store_memory_tool.py`:

```python
class TestPlannerHasGoalWrite:
    """Planner agent must have store_memory capability for goal_update route."""

    def test_planner_has_store_memory(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["planner"]
        assert "internal.store_memory" in scope

    def test_planner_retains_existing(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["planner"]
        assert "internal.get_plans" in scope
        assert "internal.get_goals" in scope
        assert "internal.search" in scope
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_store_memory_tool.py::TestPlannerHasGoalWrite -v`

Expected: FAIL — `internal.store_memory` not in planner scope.

- [ ] **Step 3: Add store_memory to Planner scope**

In `backend/src/orchestrator/agents.py`:

```python
    "planner": {
        "internal.get_plans",
        "internal.get_goals",
        "internal.search",
        "internal.store_memory",
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_store_memory_tool.py -v`

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/orchestrator/agents.py backend/tests/test_store_memory_tool.py
git commit -m "feat: give Planner store_memory capability for goal_update route"
```

---

### Task 4: Handle `ignore` Decision Properly

**Files:**
- Create: `backend/tests/test_ignore_decision.py`
- Modify: `backend/src/orchestrator/jarvis.py`
- Modify: `backend/src/services/route_resolver.py`

The `ignore` decision exists in `PlannerOutput` but has no route and no handler. It falls through to `acknowledge`, causing the Presenter to respond when the system should be silent. We add an early return in both streaming and non-streaming paths.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_ignore_decision.py`:

```python
"""Tests for the ignore decision handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.route_resolver import ALWAYS_PRESENT, DEFAULT_ROUTES
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


class TestIgnoreRouteConfig:
    """Ignore decision should not trigger Presenter."""

    def test_ignore_not_in_always_present(self):
        """ignore should NOT be in ALWAYS_PRESENT — no Presenter response."""
        assert "ignore" not in ALWAYS_PRESENT

    def test_ignore_route_exists(self):
        """ignore should have a route with empty pipeline."""
        route = next(
            (r for r in DEFAULT_ROUTES if r["decision_type"] == "ignore"),
            None,
        )
        assert route is not None
        assert route["agent_pipeline"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_ignore_decision.py -v`

Expected: FAIL — `ignore` is not in DEFAULT_ROUTES.

- [ ] **Step 3: Add `ignore` route and remove from ALWAYS_PRESENT**

In `backend/src/services/route_resolver.py`, add a route before the `acknowledge` entry:

```python
    {
        "name": "ignore",
        "description": "Silently ignore — no response, no action.",
        "decision_type": "ignore",
        "agent_pipeline": [],
        "priority": 5,
        "keywords": [],
    },
```

Verify `ignore` is NOT in the `ALWAYS_PRESENT` set (it currently isn't — `acknowledge` is but `ignore` is not). This is already correct.

- [ ] **Step 4: Add early return for `ignore` in orchestrator**

In `backend/src/orchestrator/jarvis.py`, find the `process_message` method. After the direct handlers block (around line 694) and before pipeline resolution, add:

```python
                # Ignore: no response, no action
                if decision.decision == "ignore":
                    result["status"] = "ignored"
                    result["decision"] = "ignore"
                    return result
```

In `process_message_stream`, find the equivalent location (around line 955) and add:

```python
                # Ignore: no response, no action
                if decision.decision == "ignore":
                    yield {"event": "ignored", "decision": "ignore"}
                    return
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_ignore_decision.py -v`

Expected: All PASS.

- [ ] **Step 6: Run full test suite**

Run: `cd backend && python -m pytest tests/ --ignore=tests/e2e -v 2>&1 | tail -5`

Expected: All tests pass.

- [ ] **Step 7: Lint**

Run: `cd backend && ruff check src/orchestrator/jarvis.py src/services/route_resolver.py tests/test_ignore_decision.py`

Expected: All checks passed.

- [ ] **Step 8: Commit**

```bash
git add backend/src/orchestrator/jarvis.py backend/src/services/route_resolver.py \
  backend/tests/test_ignore_decision.py
git commit -m "feat: handle ignore decision with early return — no Presenter response"
```

---

### Task 5: Expand Planner Decision Framework

**Files:**
- Modify: `backend/src/orchestrator/prompts.py:41-63`

The `JARVIS_DECISION_FRAMEWORK` only documents 7 of 19 decisions. The Planner needs guidance on all of them, especially `ignore` vs `acknowledge`.

- [ ] **Step 1: Update the decision framework**

Replace the `JARVIS_DECISION_FRAMEWORK` in `backend/src/orchestrator/prompts.py`:

```python
JARVIS_DECISION_FRAMEWORK = """\
<decision_framework>
For each input, evaluate in order:

1. Is this noise, spam, or irrelevant? -> decision: "ignore" (NO response to user)
2. Needs to READ external data (emails, calendar, PRs, messages)? -> decision: "read_source"
3. Needs background monitoring or scanning? -> decision: "observe"
4. Needs to search or recall knowledge? -> decision: "search_memory"
5. Needs deep multi-source research? -> decision: "research"
6. User wants to store a fact or note? -> decision: "remember"
7. Needs a new goal or objective? -> decision: "set_goal"
8. Needs a recurring instruction, trigger, or schedule? -> decision: "set_instruction"
9. Needs a one-time reminder? -> decision: "schedule_reminder"
10. Should be added to tomorrow's briefing? -> decision: "add_to_brief"
11. Needs a goal modified or reprioritized? -> decision: "goal_update"
12. Needs a watcher set up (alert me when...)? -> decision: "watcher_create"
13. Needs execution (write/send/create/update)? -> decision: "create_task"
14. Needs an email reply drafted? -> decision: "draft_reply"
15. Needs a recommendation or suggestion? -> decision: "recommend"
16. Needs a summary of information? -> decision: "summarize"
17. Needs clarification from user? -> decision: "ask_user"
18. Can answer directly from context? -> decision: "answer_directly"
19. Default — acknowledge and respond? -> decision: "acknowledge"

Key distinctions:
- "ignore" = NO response at all (spam, duplicate, system noise)
- "acknowledge" = respond to user but take no action
- "read_source" = fetch fresh data from external services (Gmail, Calendar, Slack)
- "search_memory" = search what Jarvis already knows (memories, entities, events)
- "research" = deep investigation across multiple sources including web
- "create_task" = any action that writes to external systems (send email, create issue, etc.)
- "draft_reply" = specifically drafting an email reply (reads thread, then drafts)
</decision_framework>
"""
```

- [ ] **Step 2: Verify prompt renders correctly**

Run: `cd backend && python -c "from src.orchestrator.prompts import JARVIS_DECISION_FRAMEWORK; print(len(JARVIS_DECISION_FRAMEWORK)); assert 'ignore' in JARVIS_DECISION_FRAMEWORK; assert 'acknowledge' in JARVIS_DECISION_FRAMEWORK; print('OK')"`

Expected: Prints byte count and "OK".

- [ ] **Step 3: Run full test suite**

Run: `cd backend && python -m pytest tests/ --ignore=tests/e2e -v 2>&1 | tail -5`

Expected: All tests pass. Some tests may check prompt contents — verify they still match.

- [ ] **Step 4: Lint**

Run: `cd backend && ruff check src/orchestrator/prompts.py`

Expected: All checks passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/orchestrator/prompts.py
git commit -m "feat: expand Planner decision framework to document all 19 decisions"
```

---

### Task 6: Update CLAUDE.md with New Architecture

**Files:**
- Modify: `CLAUDE.md`

Update the project documentation to reflect the new agent capabilities and routing changes.

- [ ] **Step 1: Update the Agent Boundaries table**

In `CLAUDE.md`, update the Operator write scope:

```markdown
| Agent | Role | Write Scope |
|-------|------|-------------|
| Observer | Read sources, detect changes, ingest events | normalized_events |
| Librarian | Extract entities, update world model, store memories | entities, relationships, memories |
| Planner | Produce task graphs, manage goals (structured JSON) | plans, plan_tasks, goal memories |
| Governor | Evaluate policies, gate approvals | policy decisions, approvals |
| Operator | Execute approved plans via tools (reads context first) | task_runs, task_steps |
| Presenter | Generate user-facing output | briefings, A2UI surfaces |
| Researcher | Deep context gathering | None (read-only) |
| Persona | Learn and store preferences | memories (preference type) |
```

- [ ] **Step 2: Add note about agentic vs scripted routes**

Add after the Agent Routing section in `CLAUDE.md`:

```markdown
## Agentic vs Scripted Execution

Routes use TWO execution modes:
- **Agentic** (message_template): Agent goes through the agent loop, discovers available tools, and autonomously decides which to call. Used by most routes.
- **Scripted** (action: "execute_plan"): GraphExecutor walks a predefined task DAG with hardcoded handlers. Used only by `create_task` for complex multi-step plans.

**Do not** add `action: "execute_plan"` to new routes unless the workflow genuinely needs DAG execution with checkpointing. Default to `message_template` so the agent can discover tools and act autonomously.

**Do not** hardcode tool-calling sequences in Python handlers. Let agents discover tools via the agent loop. The agent loop handles tool discovery, multi-turn reasoning, error recovery, and governor hooks automatically.
```

- [ ] **Step 3: Update Common Mistakes section**

Add to the Common Mistakes section:

```markdown
- Do not add `action: "execute_plan"` to routes for simple 1-2 tool workflows — use `message_template` so the agent goes through the agent loop
- Do not hardcode tool-calling sequences in `_xxx_action` handlers — let agents discover tools autonomously
- Do not give agents write capabilities without corresponding read capabilities — agents need to read context before writing (read-before-write principle)
- Do not add internal MCP tools without adding them to ALL three places: `schemas.py` (input model), `catalog.py` (tool def), `intelligence_server.py` (implementation)
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with agentic routing and agent self-sufficiency notes"
```

---

## Verification Checklist

After all tasks are complete:

- [ ] Run: `cd backend && python -m pytest tests/ --ignore=tests/e2e -v 2>&1 | tail -5` — All tests pass
- [ ] Run: `cd backend && ruff check src/ tests/` — All checks passed
- [ ] Verify Operator has read+write for: email, calendar, messaging, issue, repo, workflow
- [ ] Verify Librarian has: `internal.store_memory`, `internal.update_entity`, `internal.search`
- [ ] Verify Planner has: `internal.store_memory`, `internal.get_plans`, `internal.get_goals`, `internal.search`
- [ ] Verify Persona has: `internal.store_preference`, `internal.extract_preferences`, `internal.search`
- [ ] Verify `ignore` decision returns early with no Presenter response
- [ ] Verify `JARVIS_DECISION_FRAMEWORK` documents all 19 decisions
