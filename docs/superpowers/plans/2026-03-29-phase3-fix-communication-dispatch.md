# Phase 3: Fix Communication Tool Dispatch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 3 communication tools (`send_telegram`, `send_approval_prompt`, `push_ui_update`) callable by Claude. They exist in the MCP server but can't be dispatched because: (a) no Pydantic schemas, (b) not in `internal_tools` set, (c) `_call_internal_tool()` hardcodes `intelligence_` prefix but they need `communication_` prefix.

**Architecture:** Add 3 Pydantic models, add to `internal_tools` set, and fix `_call_internal_tool()` to use a server-prefix mapping. This is a stopgap — Phase 11 replaces the mapping with a registry lookup.

**Tech Stack:** Python, Pydantic, FastMCP, pytest

---

## Key Details

**MCP tool signatures** (from `communication_server.py`):

```python
# send_telegram: text (required), parse_mode (default "Markdown"), reply_markup (default "")
# NOTE: No user_id param — uses bot token directly

# send_approval_prompt: approval_id (required), title (required), summary (required), risk_level (default "medium")
# NOTE: No user_id param — calls send_telegram internally

# push_ui_update: surface_id (required), payload (required), user_id (required)
# NOTE: Takes user_id for Redis pub/sub channel routing — NOT for auth
```

**Dispatch fix:** `_call_internal_tool()` at line 2649 does `namespaced = f"intelligence_{tool_name}"`. Communication tools need `communication_` prefix. Add a `_INTERNAL_TOOL_SERVER` mapping dict.

**Already done (no changes needed):**
- `CAPABILITY_CATALOG`: `internal.send_telegram`, `internal.send_approval`, `internal.push_ui` already exist
- `TOOL_TO_CAPABILITY`: `send_telegram`, `send_approval_prompt`, `push_ui_update` already mapped
- `AGENT_CAPABILITY_SCOPES`: Presenter already has all 3 capabilities
- `_DEFAULT_TOOLS`: All 3 already registered

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `backend/src/orchestrator/tool_schemas.py` | Modify | Add 3 Pydantic models + 3 `TOOL_INPUT_MODELS` entries |
| `backend/src/orchestrator/jarvis.py` | Modify | Add 3 tools to `internal_tools` set + add `_INTERNAL_TOOL_SERVER` mapping + fix `_call_internal_tool()` prefix logic |
| `backend/tests/test_tool_schemas.py` | Modify | Update count 16→19, add 3 tools to expected set |
| `backend/tests/test_communication_dispatch.py` | Create | New test file for dispatch prefix fix |

---

### Task 1: Update tests (RED)

**Files:**
- Modify: `backend/tests/test_tool_schemas.py`
- Create: `backend/tests/test_communication_dispatch.py`

- [ ] **Step 1: Update tool schema test expectations**

In `test_tool_schemas.py`:
1. Update `test_tool_count_is_16` → `test_tool_count_is_19` (16 + 3 communication tools). Change assertion and docstring.
2. Add `"send_telegram"`, `"send_approval_prompt"`, `"push_ui_update"` to the expected set in `test_expected_tools_present`.

- [ ] **Step 2: Create dispatch prefix test file**

Create `backend/tests/test_communication_dispatch.py`:

```python
"""Tests for communication tool dispatch prefix resolution."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


class TestInternalToolServerMapping:
    """Verify _call_internal_tool uses correct namespace prefix per server."""

    @pytest.fixture
    def orchestrator(self):
        """Create a minimal JarvisOrchestrator for testing."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        settings = make_mock_settings()
        orch = JarvisOrchestrator(settings=settings)
        return orch

    async def test_intelligence_tool_uses_intelligence_prefix(self, orchestrator):
        """Intelligence tools should call intelligence_{tool_name}."""
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool("search", {"query": "test"})
        mock_client.call_tool.assert_called_once_with("intelligence_search", {"query": "test"})

    async def test_communication_tool_uses_communication_prefix(self, orchestrator):
        """Communication tools should call communication_{tool_name}."""
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "sent"}}
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool("send_telegram", {"text": "hello"})
        mock_client.call_tool.assert_called_once_with(
            "communication_send_telegram", {"text": "hello"}
        )

    async def test_send_approval_uses_communication_prefix(self, orchestrator):
        """send_approval_prompt should use communication_ prefix."""
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "sent"}}
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "send_approval_prompt",
            {"approval_id": "apr_001", "title": "Test", "summary": "test"},
        )
        mock_client.call_tool.assert_called_once_with(
            "communication_send_approval_prompt",
            {"approval_id": "apr_001", "title": "Test", "summary": "test"},
        )

    async def test_push_ui_uses_communication_prefix(self, orchestrator):
        """push_ui_update should use communication_ prefix."""
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "published"}}
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "push_ui_update",
            {"surface_id": "daily_brief", "payload": "{}", "user_id": "usr_001"},
        )
        mock_client.call_tool.assert_called_once_with(
            "communication_push_ui_update",
            {"surface_id": "daily_brief", "payload": "{}", "user_id": "usr_001"},
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tool_schemas.py tests/test_communication_dispatch.py -v`

Expected: Schema tests fail (count 16, missing tools). Dispatch tests fail (communication_ prefix not used — gets intelligence_ instead).

---

### Task 2: Add 3 Pydantic models to tool_schemas.py

**Files:**
- Modify: `backend/src/orchestrator/tool_schemas.py`

- [ ] **Step 4: Add SendTelegramInput**

Add after `ReportGovernorVerdictInput`, before the `TOOL_INPUT_MODELS` dict:

```python
class SendTelegramInput(BaseModel):
    """Send a message to the user via Telegram.

    Supports Markdown formatting and optional inline keyboard buttons.
    """

    text: str = Field(description="Message text (supports Markdown)")
    parse_mode: str = Field(default="Markdown", description="Format: Markdown or HTML")
    reply_markup: str = Field(
        default="", description="JSON string of inline keyboard markup (optional)"
    )
```

- [ ] **Step 5: Add SendApprovalPromptInput**

```python
class SendApprovalPromptInput(BaseModel):
    """Send an approval request with interactive Approve/Reject buttons via Telegram."""

    approval_id: str = Field(description="ID of the pending approval")
    title: str = Field(description="Approval request title")
    summary: str = Field(description="Summary of what needs approval")
    risk_level: str = Field(default="medium", description="Risk level: low, medium, high, critical")
```

- [ ] **Step 6: Add PushUiUpdateInput**

```python
class PushUiUpdateInput(BaseModel):
    """Push a dynamic UI update to the web frontend via Redis pub/sub.

    Delivers A2UI surface payloads to connected browser sessions.
    """

    surface_id: str = Field(
        description="UI surface identifier (e.g., 'daily_brief', 'approval_detail')"
    )
    payload: str = Field(description="JSON string of the A2UI surface payload")
```

Note: `push_ui_update` MCP tool takes `user_id` for Redis channel routing, but for the Pydantic schema we exclude it — it's injected at dispatch time like other internal tools.

- [ ] **Step 7: Add to TOOL_INPUT_MODELS**

Add these 3 entries:
```python
    "send_telegram": SendTelegramInput,
    "send_approval_prompt": SendApprovalPromptInput,
    "push_ui_update": PushUiUpdateInput,
```

- [ ] **Step 8: Run schema tests**

Run: `cd backend && python -m pytest tests/test_tool_schemas.py -v`

Expected: All tests PASS (GREEN).

---

### Task 3: Fix dispatch and add to internal_tools set

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py`

- [ ] **Step 9: Add _INTERNAL_TOOL_SERVER mapping**

Add at module level (near the top of the class or as a module constant), a mapping of communication tools to their server prefix:

```python
# Server prefix mapping for internal tools — default is "intelligence".
# Communication tools live on the "communication" MCP server.
# Stopgap: Phase 11 replaces this with registry lookup.
_INTERNAL_TOOL_SERVER: dict[str, str] = {
    "send_telegram": "communication",
    "send_approval_prompt": "communication",
    "push_ui_update": "communication",
}
```

- [ ] **Step 10: Fix _call_internal_tool() prefix logic**

In `_call_internal_tool()` (line ~2649), change the hardcoded prefix:

```python
# BEFORE:
        namespaced = f"intelligence_{tool_name}"

# AFTER:
        prefix = _INTERNAL_TOOL_SERVER.get(tool_name, "intelligence")
        namespaced = f"{prefix}_{tool_name}"
```

Also update the docstring to reflect the change.

- [ ] **Step 11: Add 3 tools to internal_tools set**

Add `"send_telegram"`, `"send_approval_prompt"`, `"push_ui_update"` to the `internal_tools` set:

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
            "send_telegram",
            "send_approval_prompt",
            "push_ui_update",
        }
```

Result: 18 entries (was 15).

- [ ] **Step 12: Run dispatch + orchestrator tests**

Run: `cd backend && python -m pytest tests/test_communication_dispatch.py tests/test_orchestrator.py -v`

Expected: All tests PASS.

---

### Task 4: Full suite + commit

- [ ] **Step 13: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x -q`

Expected: All tests PASS.

- [ ] **Step 14: Verify end-to-end**

```bash
cd backend && python -c "
from src.orchestrator.tool_schemas import TOOL_INPUT_MODELS
from src.integrations.capabilities import get_capability_for_tool
print(f'TOOL_INPUT_MODELS count: {len(TOOL_INPUT_MODELS)}')
assert len(TOOL_INPUT_MODELS) == 19
for tool in ('send_telegram', 'send_approval_prompt', 'push_ui_update'):
    assert tool in TOOL_INPUT_MODELS, f'{tool} missing from TOOL_INPUT_MODELS'
    cap = get_capability_for_tool(tool)
    print(f'  {tool} -> {cap}')
    assert cap is not None, f'{tool} has no capability mapping'
print('All assertions passed!')
"
```

- [ ] **Step 15: Commit**

```bash
cd backend
git add src/orchestrator/tool_schemas.py src/orchestrator/jarvis.py tests/test_tool_schemas.py tests/test_communication_dispatch.py
git commit -m "fix: enable communication tool dispatch (send_telegram, send_approval_prompt, push_ui_update)

Fix _call_internal_tool() to use communication_ prefix for communication
server tools (was hardcoded to intelligence_). Add Pydantic schemas and
internal_tools entries for all 3 communication tools.

The _INTERNAL_TOOL_SERVER mapping is a stopgap — Phase 11 replaces it
with a registry-driven server lookup.

Phase 3 of unified tool registry migration."
```

---

## Exit Criteria Checklist

- [ ] `SendTelegramInput`, `SendApprovalPromptInput`, `PushUiUpdateInput` exist in `tool_schemas.py`
- [ ] `TOOL_INPUT_MODELS` has exactly 19 entries
- [ ] `internal_tools` set has 18 entries
- [ ] `_INTERNAL_TOOL_SERVER` mapping exists with 3 communication tools
- [ ] `_call_internal_tool()` uses dynamic prefix (not hardcoded `intelligence_`)
- [ ] `send_telegram` dispatches as `communication_send_telegram`
- [ ] `send_approval_prompt` dispatches as `communication_send_approval_prompt`
- [ ] `push_ui_update` dispatches as `communication_push_ui_update`
- [ ] All tests pass (including new dispatch tests)
