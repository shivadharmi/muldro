# Fix-5: Contracts, Frontend & Dead Code Cleanup

**Priority:** P2 — correctness and maintenance
**Risk:** Low — mostly additive (ConfigDict, Literals) and deletions (dead code)
**Estimated files:** ~20-25
**Dependencies:** Should go last (after Fix-1 through Fix-4)

## Overview

Three themes:

1. **Model validation hardening** — Add `ConfigDict(extra="ignore")` to all API-boundary Pydantic models, replace bare `str` fields with `Literal` unions, and fix mutable defaults that cause shared-state bugs.
2. **Frontend fixes** — Null guard on `A2UIRenderer`, SSE tool event handling, surface store merge logic, and surface kind alignment.
3. **Dead code removal** — Purge stale `observer`/`researcher` references from source and tests, delete unused frontend types from the removed Tasks/Goals/Workflows features.

---

## Phase 1: Pydantic Model Hardening (backend)

### Task 1.1: Add `ConfigDict(extra="ignore")` to all API schemas

Add `from pydantic import ConfigDict` (if not already imported) and `model_config = ConfigDict(extra="ignore")` to every `BaseModel` subclass in these files:

**`backend/src/api/schemas.py`** — 30 models, none have `ConfigDict` today:
- `CommandRequest` (line 19)
- `CommandResponse` (line 24)
- `BriefingResponse` (line 34)
- `BriefingFeedbackRequest` (line 48)
- `BriefingFeedbackResponse` (line 58)
- `BriefingFeedbackSummary` (line 65)
- `ApprovalDecisionRequest` (line 77)
- `ApprovalResponse` (line 81)
- `TaskResponse` (line 93)
- `SearchRequest` (line 105)
- `SearchResult` (line 110)
- `SearchResponse` (line 118)
- `MeetingPrepRequest` (line 125)
- `MeetingPrepResponse` (line 130)
- `EventIngestRequest` (line 144)
- `EventIngestResponse` (line 156)
- `DashboardApproval` (line 165)
- `DashboardTask` (line 174)
- `DashboardMeeting` (line 185)
- `DashboardTrace` (line 193)
- `DashboardGoal` (line 201)
- `DashboardEvent` (line 210)
- `DashboardResponse` (line 217)
- `ApprovalDetailResponse` (line 233)
- `TaskStepResponse` (line 252)
- `TaskDetailResponse` (line 259)
- `PerceptionReportRequest` (line 275)
- `PerceptionStatusResponse` (line 282)
- `ScheduleCreateRequest` (line 296)
- `ScheduleUpdateRequest` (line 309)
- `ScheduleResponse` (line 320)
- `HealthResponse` (line 345)

**`backend/src/api/schemas/runtime.py`** — 5 models (lines 10-54):
- `RuntimeStepResponse`
- `RuntimeRunResponse`
- `RuntimeEventResponse`
- `AgentWorkloadResponse`
- `RuntimeSummaryResponse`

**`backend/src/api/routes_trust.py`** — 11 models (lines 19-82):
- `TrustRiskLevel`, `TrustCapabilityEntry`, `TrustDashboardResponse`, `TrustCapabilityDetailRisk`, `TrustCapabilityDetailResponse`, `CeilingRequest`, `CeilingResponse`, `ResetResponse`, `TimePolicyRule`, `TimePoliciesResponse`, `TimePoliciesRequest`

**`backend/src/api/routes_insights.py`** — 2 models (lines 18-23):
- `DismissRequest`, `DismissResponse`

Also add `ConfigDict` import where missing (`from pydantic import BaseModel, ConfigDict`).

### Task 1.2: Add `Literal` constraints to contracts.py

**`backend/src/orchestrator/contracts.py`:**

- **Line 195** — `PolicyDecision.risk_level: str = "low"` change to:
  ```python
  risk_level: Literal["none", "low", "medium", "high"] = "low"
  ```

- **Line 289** — `StepState.status: str` change to:
  ```python
  status: Literal["pending", "executing", "completed", "failed", "approval_needed", "user_action"]
  ```

- **Line 327** — `SurfaceUpdate.phase: str` change to:
  ```python
  phase: Literal["planning", "plan_ready", "executing", "approval_needed", "completed", "failed", "partial"]
  ```

Ensure `Literal` is imported from `typing` (already imported at top of file — verify).

### Task 1.3: Fix mutable defaults in `ui/contracts.py`

**`backend/src/ui/contracts.py`:**

Add `from pydantic import Field` to imports (if not already present).

- **Line 82** — `A2UIAction.payload: dict = {}` change to:
  ```python
  payload: dict = Field(default_factory=dict)
  ```

- **Line 88** — `A2UIComponent.properties: dict = {}` change to:
  ```python
  properties: dict = Field(default_factory=dict)
  ```

- **Line 89** — `A2UIComponent.children: list["A2UIComponent"] = []` change to:
  ```python
  children: list["A2UIComponent"] = Field(default_factory=list)
  ```

- **Line 90** — `A2UIComponent.actions: list[A2UIAction] = []` change to:
  ```python
  actions: list[A2UIAction] = Field(default_factory=list)
  ```

- **Line 111** — `A2UISurface.children: list[A2UIComponent] = []` change to:
  ```python
  children: list[A2UIComponent] = Field(default_factory=list)
  ```

- **Line 112** — `A2UISurface.metadata: dict = {}` change to:
  ```python
  metadata: dict = Field(default_factory=dict)
  ```

---

## Phase 2: Frontend Fixes

### Task 2.1: Add null guard to `A2UIRenderer`

**`frontend/src/components/a2ui/renderer.tsx:160`**

Change:
```tsx
{surface.children.map((child) => renderComponent(child, onAction))}
```
To:
```tsx
{(surface.children ?? []).map((child) => renderComponent(child, onAction))}
```

### Task 2.2: Add `tool_call`/`tool_result` SSE handling

**`frontend/src/components/jarvis/chat-panel.tsx`**

The SSE switch statement (lines 213-342) has no cases for `tool_call` or `tool_result`. Add two new cases before the `case "done"` block (around line 335):

```tsx
case "tool_call":
  updateAssistant((m) => ({
    ...m,
    agents: m.agents.map((a) =>
      a.agent === event.agent && a.status === "running"
        ? {
            ...a,
            toolCalls: [
              ...a.toolCalls,
              {
                tool: event.tool || "unknown",
                status: "running" as const,
              },
            ],
          }
        : a
    ),
  }));
  break;

case "tool_result":
  updateAssistant((m) => ({
    ...m,
    agents: m.agents.map((a) =>
      a.agent === event.agent && a.status === "running"
        ? {
            ...a,
            toolCalls: a.toolCalls.map((tc, i) =>
              i === a.toolCalls.length - 1
                ? { ...tc, status: "done" as const }
                : tc
            ),
          }
        : a
    ),
  }));
  break;
```

Also update the `ChatSSEEvent` type in the relevant types file to include `tool_call` and `tool_result` event types, and add `tool?: string` field.

### Task 2.3: Fix `updateSurface` merge logic

**`frontend/src/stores/surface-store.ts:82-93`**

The current code unconditionally overwrites all fields from the update, including `steps` which may be undefined. Change to selective merge:

```tsx
updateSurface: (surfaceId, update) =>
  set((s) => {
    const idx = s.surfaces.findIndex((sf) => sf.id === surfaceId);
    if (idx === -1) return s;
    const prev = s.surfaces[idx];
    const next = [...s.surfaces];
    next[idx] = {
      ...prev,
      ...(update.phase !== undefined && { phase: update.phase }),
      ...(update.steps && update.steps.length > 0 && { steps: update.steps }),
      ...(update.current_step !== undefined && { current_step: update.current_step }),
      ...(update.progress !== undefined && { progress: update.progress }),
      ...(update.approval !== undefined && { approval: update.approval }),
      ...(update.results !== undefined && { results: update.results }),
    };
    return { surfaces: next };
  }),
```

### Task 2.4: Resolve `"execution"` surface kind mismatch

**`frontend/src/lib/types/surfaces.ts:15`** — Remove `"execution"` from the frontend `SurfaceKind` union. The backend `SurfaceKind` (in `backend/src/ui/contracts.py:25-37`) does not include it, so it never matches.

Also remove `"proactive_insight"` if it does not exist in the backend `SurfaceKind` literal. Verify by checking `backend/src/ui/contracts.py:25-37` — current backend values are: `summary`, `briefing`, `plan`, `checklist`, `approval`, `comparison`, `alert`, `timeline`, `table`, `recommendation`, `activity`.

Updated frontend type:
```tsx
export type SurfaceKind =
  | "summary"
  | "briefing"
  | "plan"
  | "checklist"
  | "approval"
  | "comparison"
  | "alert"
  | "timeline"
  | "table"
  | "recommendation"
  | "activity"
  | "proactive_insight";
```

Note: Keep `proactive_insight` only if it is used in frontend-specific logic; otherwise remove it too. Search frontend for `proactive_insight` usage before deciding.

---

## Phase 3: Dead Code Removal (backend)

### Task 3.1: Remove observer/researcher references from source

**`backend/src/orchestrator/budget.py:33-42`** — Delete the entire `AGENT_MODELS` dict. Verify it is not imported anywhere (it is unused — agent model selection comes from `agents.py` `AGENT_DEFINITIONS`).

**`backend/src/services/agent_registry.py:22-42`** — Update `_DEFAULT_DISPLAY_NAMES` and `_DEFAULT_DESCRIPTIONS`:
- Remove `"observer"` and `"researcher"` keys
- Add `"perceiver"` key with appropriate display name and description

**`backend/src/orchestrator/jarvis.py:52-63`** — Remove `"research_started"` and `"research_completed"` from the `AGENT_EVENT_TYPES` set.

**`backend/src/orchestrator/agents.py`** — Update stale comments:
- Line 27: `# From observer — external data source reads` change to `# External data source reads (perception)`
- Line 59: `# From observer — internal observation tools` change to `# Internal observation tools`
- Line 64: `# From researcher — knowledge + web` change to `# Knowledge + web search`

**`backend/scripts/explore_tools.py`** — Remove imports of deleted symbols (lines 277, 288, 328-357, 511-555). If the script is entirely dead, consider deleting the file. Otherwise, update to use current imports.

### Task 3.2: Remove dead frontend types

**`frontend/src/lib/types.ts`** — Delete the following dead exported types (and their associated input types):

- Lines 137-162: `Task`, `TaskStep`, `TaskDetail` (standalone tasks removed in product redesign)
- Lines 290-315: `StandaloneTask`, `StandaloneTaskCreateInput` (standalone tasks)
- Lines 317-338: `Goal`, `GoalCreateInput` (goals absorbed into memory system)
- Lines 354-361: `Workflow` (workflows removed)
- Lines 164-209: `Schedule`, `ScheduleCreateInput`, `ScheduleUpdateInput` (user-facing schedule CRUD removed)

Verify each type has zero imports before deleting. Use `grep -r "Task\b\|TaskStep\|TaskDetail\|StandaloneTask\|Goal\b\|GoalCreateInput\|Workflow\b\|Schedule\b\|ScheduleCreateInput\|ScheduleUpdateInput" frontend/src/` to confirm.

---

## Phase 4: Test Fixture Updates

### Task 4.1: Update all test fixtures using deleted agent names

All tests referencing `"observer"` or `"researcher"` agent names must be updated to use `"perceiver"` (the merged agent).

**`backend/tests/e2e/test_03_service_chains.py:166-178`**
- Replace `"observer"` and `"researcher"` with `"perceiver"` in agent name assertions
- Update agent count assertion from `>= 8` to `>= 7` (or exact count matching current agent list)

**`backend/tests/test_contracts.py:29,40-45`**
- Replace `"observer"`/`"researcher"` agent name fixtures with `"perceiver"`

**`backend/tests/golden/test_governor_policies.py:28-34`**
- Replace dead agent names in governor test fixtures

**`backend/tests/test_unified_dispatch.py:288,332`**
- Change `SubAgent(name="researcher")` to `SubAgent(name="perceiver")`

**`backend/tests/test_foundation_hardening.py:531`**
- Change `record_from_span(agent_name="observer")` to `record_from_span(agent_name="perceiver")`

**`backend/tests/test_context_assembler.py:32,147`**
- Change `_assemble_context("observer"/"researcher")` to `_assemble_context("perceiver")`

**`backend/tests/test_trace_store.py`** and **`backend/tests/test_alerting.py`**
- Update any stale agent name fixtures from `"observer"`/`"researcher"` to `"perceiver"`

After updating, run: `pytest tests/ -v -k "observer or researcher"` to confirm no remaining references.

---

## Verification

- [ ] `ruff check backend/src/ backend/tests/` passes
- [ ] `ruff format backend/src/ backend/tests/` produces no changes
- [ ] `pytest backend/tests/ -v` — all tests pass
- [ ] `cd frontend && npm run build` — no TypeScript errors
- [ ] `cd frontend && npm run lint` — no ESLint errors
- [ ] Grep for `"observer"` and `"researcher"` in `backend/src/` returns zero hits (except historical docs/comments if intentional)
- [ ] Grep for `= {}` and `= []` in Pydantic models returns zero hits in `ui/contracts.py`
- [ ] All `BaseModel` subclasses in `schemas.py`, `schemas/runtime.py`, `routes_trust.py`, `routes_insights.py` have `ConfigDict(extra="ignore")`
