# A2UI Architecture Hardening — Full Lifecycle Cleanup

**Date:** 2026-04-13
**Branch:** `improve-surface-design-v1`
**Status:** Design approved, pending implementation

## Context

An architectural review of the A2UI (Agent-to-UI) system identified 14 issues spanning dead code, type safety gaps, competing surface models, missing rate limiting, inconsistent delivery shapes, boundary violations, and incomplete feature coverage. These issues conflict with the Soul document's principles of "calm competence," "high signal density with low cognitive noise," and "elegant instrument, not chaotic control room."

This spec addresses ALL 14 issues across 4 phases, with each phase independently shippable and building on the prior.

## Issue Inventory

| # | Issue | Severity | Phase |
|---|---|---|---|
| 1 | No surface push rate limiting | High | 2 |
| 2 | No workspace surface cap / LRU eviction | High | 2 |
| 3 | Two competing surface models (old `A2UISurface.children[]` vs new preview+detail) | Medium | 1 |
| 4 | `briefing_surface()` is dead code (only caller: 1 test) | Low | 1 |
| 5 | REST returns `dict[str, Any]`, WS uses typed `WorkspaceSurfacePush` — shape divergence | Medium | 2 |
| 6 | `SurfaceKind` duplicated in `contracts.py` and `WorkspaceSurfacePush.kind` | Low | 1 |
| 7 | `_derive_surface_kind` + `_build_surface_preview_from_plan` live in `jarvis.py` not surface layer | Medium | 1 |
| 8 | `properties: dict` has zero per-type validation | Medium | 2 |
| 9 | `IMAGE` and `COMMAND_PALETTE` declared in enum, fully unimplemented | Low | 1 |
| 10 | `ExecutionSurface` in frontend renderer has no backend enum counterpart | Low | 1 |
| 11 | `children?` on frontend `WorkspaceSurface` is vestige of old model | Low | 1 |
| 12 | Missing detail tabs for 6 surface kinds | Medium | 4 |
| 13 | Recommendation detail tabs paper-thin (no drill-down) | Medium | 4 |
| 14 | `WorkspaceSurfacesResponse` uses `list[dict]` not typed Pydantic model | Low | 2 |

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Properties validation | Full typed discriminated union per component type | Catches typos at construction time; agents producing JSON get validated |
| Surface cap / eviction | Priority-weighted recency, cap of 20 | Respects natural priority hierarchy; approvals never evicted while active |
| Push rate limiting | Orchestrator-level Redis sliding window (5 workspace/min, 3 insight/30min) | Centralized at the two emission points; matches Notifier's pattern |
| REST/WS unification | Converge on `WorkspaceSurfacePush` for both paths | Eliminates shape divergence at source; frontend receives one type |
| Missing detail tabs | All 12 surface kinds get detail tabs | Complete the feature across all kinds |
| Surface kind production | Presenter-driven for all non-system surfaces | Aligns with agent boundaries: "Only Presenter talks to the user" |
| Activity surfaces | On-demand via Presenter (not scheduler) | Avoids "notification machine" anti-pattern; history page covers always-available view |

---

## Phase 1: Clean Foundation

**Goal:** Remove dead code, unify type definitions, fix boundary violations. Zero behavior change.

### 1.1 Dead code removal

| Item | File | Action |
|---|---|---|
| `briefing_surface()` | `backend/src/ui/renderer.py:420-543` | Delete function |
| `surface()` helper | `backend/src/ui/renderer.py:383-388` | Delete function |
| `A2UISurface` import | `backend/src/ui/renderer.py:10` | Remove from imports |
| `test_briefing_surface` | `backend/tests/test_orchestrator.py:495-510` | Delete test |
| `ComponentType.IMAGE` | `backend/src/ui/contracts.py:76` | Remove from enum |
| `ComponentType.COMMAND_PALETTE` | `backend/src/ui/contracts.py:78` | Remove from enum |
| `children?` field | `frontend/src/stores/surface-store.ts:18` | Remove from `WorkspaceSurface` interface |

`A2UISurface` model stays in `contracts.py` — the frontend TypeScript types still mirror it. Cleaned up when frontend fully stops referencing it.

### 1.2 Fix `SurfaceKind` duplication

**Problem:** `SurfaceKind` is a `Literal` at `ui/contracts.py:25-38` AND duplicated inline in `WorkspaceSurfacePush.kind` at `orchestrator/contracts.py:236-249`.

**Fix:** Import and reuse:

```python
# orchestrator/contracts.py
from src.ui.contracts import SurfaceKind

class WorkspaceSurfacePush(BaseModel):
    kind: SurfaceKind  # single source of truth
```

### 1.3 Move UI functions out of `jarvis.py`

Move to new file `backend/src/services/surface_mapping.py`:
- `_derive_surface_kind()` (jarvis.py ~line 90)
- `_build_surface_preview_from_plan()` (jarvis.py ~line 124)

`jarvis.py` imports and calls them. No logic change — pure relocation.

**Why a new file:** `surface_builder.py` is the REST-path builder (DB → surfaces). These functions are the WS-path builder (PlanOutput → surface push). Different concerns, diverge further in Phase 3 when Presenter takes over.

### 1.4 Fix phantom `ExecutionSurface` type

Remove `case "ExecutionSurface"` from `frontend/src/components/a2ui/renderer.tsx:146`. The backend `ComponentType` enum has no `EXECUTION_SURFACE` value — this case can never be hit through the standard A2UI dispatch. The `A2UIExecutionSurface` component is used directly by the execution surface card component, not through the renderer dispatch.

### Files touched

- `backend/src/ui/contracts.py` — remove IMAGE, COMMAND_PALETTE from enum
- `backend/src/ui/renderer.py` — delete `briefing_surface()`, `surface()`, remove A2UISurface import
- `backend/src/orchestrator/contracts.py` — import SurfaceKind, use in WorkspaceSurfacePush
- `backend/src/orchestrator/jarvis.py` — import from surface_mapping instead of local functions
- `backend/src/services/surface_mapping.py` — **new file**, relocated functions
- `backend/tests/test_orchestrator.py` — delete `test_briefing_surface`
- `frontend/src/stores/surface-store.ts` — remove `children?` field
- `frontend/src/components/a2ui/renderer.tsx` — remove ExecutionSurface case

### Testing

- All existing tests pass (dead code removal doesn't break callers — verified no callers exist)
- `ruff check` passes on modified files
- Frontend `npm run build` passes
- Surface push via WebSocket still works (import relocation is transparent)

---

## Phase 2: Typed Contracts + Unified Delivery

**Goal:** Type-safe component properties, converge REST/WS on one model, add rate limiting and surface cap.

### 2.1 Typed component properties

**New file:** `backend/src/ui/component_properties.py`

Per-component Pydantic property models:

**Text family:**
- `TextProperties` — `text: str`, `variant: Literal["heading", "body", "caption"]`
- `CodeBlockProperties` — `code: str`, `language: str`
- `BadgeProperties` — `label: str`, `variant: Literal["default", "success", "warning", "danger"]`
- `AlertProperties` — `message: str`, `severity: Literal["info", "warning", "error", "success"]`, `title: str | None`

**Input family:**
- `ButtonProperties` — `label: str`, `variant: Literal["primary", "secondary", "danger", "ghost"]`
- `TextFieldProperties` — `label: str`, `placeholder: str`, `value: str`
- `SelectProperties` — `label: str`, `options: list[dict]`, `value: str`
- `ToggleProperties` — `label: str`, `checked: bool`

**Data family:**
- `TableProperties` — `columns: list[dict]`, `rows: list[dict]`, `sortable: bool`
- `DataGridProperties` — `columns: list[dict]`, `rows: list[dict]`, `page_size: int`
- `TimelineProperties` — `events: list[dict]`
- `MetricProperties` — `label: str`, `value: str | int | float`, `change: str | None`, `trend: str | None`
- `ProgressProperties` — `value: float`, `max: float`, `label: str | None`
- `ChartProperties` — `chart_type: str`, `data: dict`, `title: str`

**Display family:**
- `AvatarProperties` — `name: str`, `url: str | None`, `size: Literal["sm", "md", "lg"]`
- `StatusIndicatorProperties` — `status: str`, `label: str`
- `EntityCardProperties` — `name: str`, `entity_type: str`, `entity_id: str`, `attributes: dict | None`
- `MemoryCardProperties` — `fact_text: str`, `memory_type: str`, `source: str`, `confidence: float`

**Specialized family:**
- `ExecutionTraceProperties` — `steps: list[dict]`, `status: str`
- `KanbanBoardProperties` — `columns: list[dict]`
- `CalendarProperties` — `events: list[dict]`, `view: Literal["day", "week", "month"]`

**Layout (properties needed):**
- `TabsProperties` — `active_tab: int`, `labels: list[str]`
- `ModalProperties` — `title: str`, `open: bool`

**Layout (no properties):** Card, Row, Column, List, Divider, Form — keep `properties: dict = {}`.

**Registry:**

```python
PROPERTY_MODELS: dict[str, type[BaseModel]] = {
    "Text": TextProperties,
    "CodeBlock": CodeBlockProperties,
    "Badge": BadgeProperties,
    # ... all 22 models mapped
}
```

**Validation on `A2UIComponent`:**

```python
class A2UIComponent(BaseModel):
    type: str
    id: str
    properties: dict = Field(default_factory=dict)
    children: list["A2UIComponent"] = Field(default_factory=list)
    actions: list[A2UIAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_properties(self) -> "A2UIComponent":
        model = PROPERTY_MODELS.get(self.type)
        if model is not None:
            model(**self.properties)  # raises ValidationError on bad shape
        return self
```

`properties` remains `dict` on the wire — zero frontend impact. Validation is backend-only at construction time.

**Builder function updates (`renderer.py`):**

Each builder constructs the property model first for validation, then dumps to dict:

```python
def button(id: str, label: str, variant: str = "primary", action_payload: dict | None = None):
    props = ButtonProperties(label=label, variant=variant)
    actions = [A2UIAction(type="click", payload=action_payload)] if action_payload else []
    return A2UIComponent(type="Button", id=id, properties=props.model_dump(), actions=actions)
```

### 2.2 Converge REST/WS on `WorkspaceSurfacePush`

**Extend `WorkspaceSurfacePush`** with fields currently only on REST path:

```python
class WorkspaceSurfacePush(BaseModel):
    # Existing fields
    type: Literal["surface"] = "surface"
    id: str
    kind: SurfaceKind
    preview: Any
    detail_config: Any | None = None
    decision: str | None = None
    source_run_id: str | None = None
    response_preview: str | None = None
    created_at: str = ""
    ttl_hours: int = 24

    # New — merged from REST-only fields
    trust_context: dict[str, str] | None = None
    insight_data: dict | None = None
    phase: str | None = None
    steps: list | None = None
    current_step: str | None = None
    progress: str | None = None
    approval: dict | None = None
    results: dict | None = None
```

**Change `SurfaceService`:** Return `list[WorkspaceSurfacePush]` instead of `list[dict[str, Any]]`. Each `_build_*_surfaces` method constructs `WorkspaceSurfacePush` instances.

**Change REST endpoint:** `WorkspaceSurfacesResponse.surfaces` becomes `list[WorkspaceSurfacePush]`.

**Frontend cleanup:** Remove `as SurfaceKind` casts in `page.tsx` and `chat/page.tsx`. Type is now guaranteed.

### 2.3 Surface push rate limiting

Add `_check_surface_rate()` to `JarvisOrchestrator`:

```python
async def _check_surface_rate(self, user_id: str, surface_type: str) -> bool:
    """Return True if push is allowed. Uses Redis sliding window."""
    event_bus = await self._ensure_event_bus()
    if not event_bus or not event_bus._redis:
        return True  # no Redis → allow

    redis = event_bus._redis
    if surface_type == "insight":
        key = f"jarvis:surface_rate:insight:{user_id}"
        limit, window = 3, 1800  # 3 per 30 min
    else:
        key = f"jarvis:surface_rate:workspace:{user_id}"
        limit, window = 5, 60  # 5 per min

    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    return count <= limit
```

Called at the top of `_push_workspace_surface()` and `_push_insight_surface()`. Rate-exceeded surfaces are silently dropped (logged at debug). No user-facing error.

### 2.4 Workspace surface cap with priority-weighted eviction

```python
MAX_WORKSPACE_SURFACES = 20

PRIORITY_TIERS: dict[str, int] = {
    "approval": 0,
    "plan": 1,
    "alert": 2,
    "briefing": 3,
    "proactive_insight": 4,
    "recommendation": 5,
    "summary": 6,
    "checklist": 6,
    "comparison": 6,
    "timeline": 6,
    "table": 6,
    "activity": 6,
}
```

Applied in `SurfaceService.build_workspace_surfaces()` after aggregating all surfaces. Sort by `(tier, -created_at)`, truncate to `MAX_WORKSPACE_SURFACES`.

Frontend defense-in-depth: `useSurfaceStore.setSurfaces()` also applies the cap.

### Files touched

- `backend/src/ui/component_properties.py` — **new file**, 22 property models + PROPERTY_MODELS registry
- `backend/src/ui/contracts.py` — add model_validator to A2UIComponent
- `backend/src/ui/renderer.py` — update all 36 builder functions to use property models
- `backend/src/orchestrator/contracts.py` — extend WorkspaceSurfacePush with REST-only fields
- `backend/src/services/surface_builder.py` — return `list[WorkspaceSurfacePush]`, add cap/eviction
- `backend/src/services/surface_mapping.py` — add PRIORITY_TIERS constant
- `backend/src/orchestrator/jarvis.py` — add `_check_surface_rate()`, call in push functions
- `backend/src/api/routes_ui.py` — update response model to use WorkspaceSurfacePush
- `frontend/src/stores/surface-store.ts` — add cap enforcement in setSurfaces
- `frontend/src/app/page.tsx` — remove `as SurfaceKind` casts
- `frontend/src/app/chat/page.tsx` — remove `as SurfaceKind` casts
- `backend/src/services/surface_detail_builders.py` — update to use typed property builders

### Testing

- Unit tests for each property model (valid + invalid inputs)
- Unit test for `_check_surface_rate` with mock Redis
- Unit test for priority-weighted eviction (verify approvals survive, stale summaries evict)
- Integration test: verify REST and WS return same shape
- All existing A2UI tests pass with new validation

---

## Phase 3: Presenter-Driven Surface Architecture

**Goal:** Move surface kind decisions from hardcoded `_derive_surface_kind()` to the Presenter agent. All 12 surface kinds become producible.

### 3.1 Architectural shift

**Current flow:**
```
PlanOutput → _derive_surface_kind() [hardcoded heuristic]
           → _build_surface_preview_from_plan() [hardcoded]
           → WorkspaceSurfacePush → Redis → frontend
```

**New flow:**
```
PlanOutput + execution results → Presenter agent
           → structured SurfaceSpec JSON in response
           → parse + validate → WorkspaceSurfacePush → Redis → frontend
```

### 3.2 `SurfaceSpec` — Presenter output contract

New model in `orchestrator/contracts.py`:

```python
class SurfaceSpec(BaseModel):
    """Surface specification produced by the Presenter agent."""
    model_config = ConfigDict(extra="ignore")

    should_surface: bool = False
    kind: SurfaceKind
    title: str                    # max 80 chars
    subtitle: str | None = None   # max 120 chars
    status: Literal[
        "pending", "running", "completed", "failed",
        "awaiting_approval", "cancelled", "proposal"
    ] | None = None
    priority: Literal["low", "medium", "high", "critical"] | None = None
    metrics: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _cap_title(cls, v: str) -> str:
        return v[:80]

    @field_validator("subtitle")
    @classmethod
    def _cap_subtitle(cls, v: str | None) -> str | None:
        return v[:120] if v else None
```

### 3.3 Presenter prompt additions

Add `SURFACE_GENERATION` section to `PRESENTER_PROMPT` in `prompts.py`:

```
## Surface Generation

When your response has visual value beyond chat text, include a surface specification
in a ```json:surface``` fenced block.

| Kind | When to use |
|------|-------------|
| summary | Single-topic synthesis, lookup result, brief answer with sources |
| briefing | Daily overview, multi-source digest, morning context |
| plan | Multi-step execution with progress tracking |
| checklist | Sequential low-risk tasks in the same category |
| comparison | Side-by-side evaluation of 2+ alternatives |
| alert | Blocked execution, system warning, urgent attention needed |
| timeline | Chronologically ordered events or history narrative |
| table | Structured tabular data, multiple entities with shared attributes |
| recommendation | Suggested action based on observed patterns |
| activity | Summary of recent Jarvis actions (only when user asks) |

Do NOT create surfaces for:
- Simple conversational replies
- Information that fits naturally in chat text alone
- `approval` kind (system-generated by TrustEngine)
- `proactive_insight` kind (system-generated by perception pipeline)

When creating a surface, still include a brief chat response. The surface provides
the detailed, persistent, interactive view.

For structured data (comparison options, table rows, timeline events), include a
```json:surface_data``` block with the structured payload alongside the surface spec.
```

### 3.4 Integration in `jarvis.py`

**Replace** hardcoded surface push:

```python
# OLD
surface_id = await self._push_workspace_surface(plan, user_id, workspace_id, run_id, response_text)

# NEW
surface_spec = _extract_surface_spec(response_text)
if surface_spec and surface_spec.should_surface:
    surface_id = await self._push_presenter_surface(
        spec=surface_spec, user_id=user_id, workspace_id=workspace_id,
        run_id=run_id, response_text=response_text,
    )
```

**New `_push_presenter_surface()`:**
- Checks rate limit via `_check_surface_rate()`
- Builds `SurfacePreview` from `SurfaceSpec` fields
- Builds `DetailConfig` via `build_detail_config(spec.kind, surface_id)`
- Constructs `WorkspaceSurfacePush`
- Publishes to Redis + persists to `ui_surfaces` table
- Stores optional `surface_data` (from `json:surface_data` block) in `UISurface.payload["surface_data"]`

### 3.5 `_extract_surface_spec` parser

New function in `surface_mapping.py`:

```python
def _extract_surface_spec(response_text: str) -> SurfaceSpec | None:
    """Extract SurfaceSpec from ```json:surface``` block in Presenter response.
    Returns None if not found or invalid. Best-effort — degrades to chat-only."""

def _extract_surface_data(response_text: str) -> dict | None:
    """Extract structured data from ```json:surface_data``` block.
    Used by detail tab builders for comparison, table, timeline, checklist kinds."""
```

### 3.6 Delete hardcoded mapping functions

After integration is complete, delete from `surface_mapping.py`:
- `_derive_surface_kind()` — fully replaced by Presenter's kind selection
- `_build_surface_preview_from_plan()` — fully replaced by Presenter's SurfaceSpec

### 3.7 Non-Presenter surface paths (unchanged)

Two paths bypass the Presenter intentionally:
- **Approval surfaces** — `TrustEngine` → hardcoded `kind="approval"` in GraphExecutor
- **Insight surfaces** — `_push_insight_surface()` → hardcoded `kind="proactive_insight"`

These are system-generated gates. The Presenter prompt explicitly says not to produce these kinds.

### Files touched

- `backend/src/orchestrator/contracts.py` — add `SurfaceSpec` model
- `backend/src/orchestrator/prompts.py` — add SURFACE_GENERATION section to PRESENTER_PROMPT
- `backend/src/orchestrator/jarvis.py` — replace `_push_workspace_surface` calls with `_push_presenter_surface`, add new method
- `backend/src/services/surface_mapping.py` — add `_extract_surface_spec()`, `_extract_surface_data()`, delete old functions
- `backend/src/models/ui_state.py` — no change (payload JSONB already stores arbitrary data)

### Testing

- Unit tests for `_extract_surface_spec` (valid JSON, malformed, missing block, edge cases)
- Unit tests for `SurfaceSpec` validation (title capping, kind validation)
- Integration test: mock Presenter response with surface block → verify WorkspaceSurfacePush emitted
- Integration test: Presenter response without surface block → verify no surface pushed
- Verify approval and insight surfaces still work via their dedicated paths

---

## Phase 4: Detail Tabs + Enrichment

**Goal:** Every surface kind gets meaningful detail tabs. Recommendation tabs get drill-down. New surface kinds get structured data support.

### 4.1 Updated `_TABS_BY_KIND`

```python
_TABS_BY_KIND: dict[str, list[tuple[str, str]]] = {
    # Existing (approval, plan, summary, briefing unchanged)
    "plan":              [("overview", "Overview"), ("context", "Context"), ("execution", "Execution")],
    "summary":           [("overview", "Overview"), ("sources", "Sources"), ("context", "Context")],
    "briefing":          [("priorities", "Priorities"), ("events", "Events"), ("actions", "Actions")],
    "approval":          [("request", "Request"), ("risk", "Risk"), ("history", "History")],

    # Modified
    "recommendation":    [("overview", "Overview"), ("evidence", "Evidence"), ("context", "Context")],
    "alert":             [("overview", "Overview"), ("diagnostics", "Diagnostics")],

    # New
    "checklist":         [("items", "Items"), ("context", "Context")],
    "comparison":        [("options", "Options"), ("criteria", "Criteria")],
    "timeline":          [("events", "Events"), ("context", "Context")],
    "table":             [("data", "Data"), ("sources", "Sources")],
    "activity":          [("runs", "Recent Runs"), ("stats", "Stats")],
    "proactive_insight": [("signal", "Signal"), ("actions", "Actions"), ("context", "Context")],
}
```

### 4.2 New tab builders

All in `surface_detail_builders.py`:

**Checklist:**
- `build_checklist_items` — TaskSteps rendered as check items (✓ completed, ○ pending)
- `build_checklist_context` — memories/entities from context_pack (reuse pattern from plan)

**Comparison:**
- `build_comparison_options` — reads `payload["surface_data"]` for structured option data, renders as table or side-by-side cards. Falls back to `response_preview`.
- `build_comparison_criteria` — reads `payload["surface_data"]["criteria"]`, renders as badge list

**Timeline:**
- `build_timeline_events` — reads `payload["surface_data"]` or falls back to NormalizedEvents. Renders via `renderer.timeline()`.
- `build_timeline_context` — memories/entities from context_pack

**Table:**
- `build_table_data` — reads `payload["surface_data"]` → `{columns, rows}`. Renders via `renderer.table()` or `renderer.data_grid()`.
- `build_table_sources` — source attribution from linked TaskStep output_data

**Activity:**
- `build_activity_runs` — queries last 24h TaskRuns. Renders status badge + source + timing.
- `build_activity_stats` — aggregates: completed count, failed count, success rate. Renders as metric components.

**Proactive insight:**
- `build_insight_signal` — reads `InsightSurfaceData` from payload. Renders signal_source badge, signal_summary, relevance_score metric, relevance_reasoning.
- `build_insight_actions` — reads `suggested_actions`. Renders description + capability badge + execute button.
- `build_insight_context` — reads `related_goals`. Fetches related memories if workspace context available.

### 4.3 Enriched recommendation evidence tab

Replace thin `build_recommendation_context` with `build_recommendation_evidence`:
- If "failed" in title → query failed TaskRuns (last 24h), show error details with run links
- If "source"/"failing" in title → query PerceptionState with `circuit_state="open"`, show source error details
- Fallback to generic context if neither pattern matches

### 4.4 New alert diagnostics tab

`build_alert_diagnostics`:
- Load TaskSteps for linked run
- Show which step failed/blocked with error details
- Show retry count and timing information

### 4.5 Presenter payload convention

For structured data (comparison, table, timeline, checklist):
- Presenter includes `json:surface_data` block alongside `json:surface` block
- Push function stores in `UISurface.payload["surface_data"]`
- Detail tab builders read from `payload["surface_data"]`, falling back to `response_preview` text

### 4.6 Ephemeral surface prefix map update

Add `surf_` prefix handling in `routes_surface_detail.py`:
```python
"surf_": ("_from_payload", "surface_id")  # kind from UISurface.surface_type
```

For Presenter-generated surfaces (all use `surf_` prefix), kind is resolved from the persisted `UISurface.surface_type`.

### 4.7 TAB_BUILDERS registry

15 existing + 15 new = **30 tab builders** across 12 surface kinds:

```python
TAB_BUILDERS = {
    # Existing 15
    ("plan", "overview"): build_plan_overview,
    ("plan", "context"): build_plan_context,
    ("plan", "execution"): build_plan_execution,
    ("summary", "overview"): build_summary_overview,
    ("summary", "sources"): build_summary_sources,
    ("summary", "context"): build_summary_context,
    ("briefing", "priorities"): build_briefing_priorities,
    ("briefing", "events"): build_briefing_events,
    ("briefing", "actions"): build_briefing_actions,
    ("approval", "request"): build_approval_request,
    ("approval", "risk"): build_approval_risk,
    ("approval", "history"): build_approval_history,
    ("recommendation", "overview"): build_recommendation_overview,
    ("alert", "overview"): build_alert_overview,

    # Modified 1
    ("recommendation", "evidence"): build_recommendation_evidence,

    # New 14
    ("alert", "diagnostics"): build_alert_diagnostics,
    ("checklist", "items"): build_checklist_items,
    ("checklist", "context"): build_checklist_context,
    ("comparison", "options"): build_comparison_options,
    ("comparison", "criteria"): build_comparison_criteria,
    ("timeline", "events"): build_timeline_events,
    ("timeline", "context"): build_timeline_context,
    ("table", "data"): build_table_data,
    ("table", "sources"): build_table_sources,
    ("activity", "runs"): build_activity_runs,
    ("activity", "stats"): build_activity_stats,
    ("proactive_insight", "signal"): build_insight_signal,
    ("proactive_insight", "actions"): build_insight_actions,
    ("proactive_insight", "context"): build_insight_context,
}
```

### Files touched

- `backend/src/ui/renderer.py` — update `_TABS_BY_KIND` with all 12 kinds
- `backend/src/services/surface_detail_builders.py` — add 14 new builders, modify 1 existing
- `backend/src/api/routes_surface_detail.py` — add `surf_` prefix to `_PREFIX_MAP`

### Testing

- Unit test for each new tab builder (valid surface, missing data, empty results)
- Integration test: detail endpoint returns correct tabs for each surface kind
- Integration test: Presenter-generated surfaces with `surface_data` → detail tabs render structured content
- Verify existing plan/summary/briefing/approval detail tabs unchanged

---

## Non-Goals

- Frontend component visual redesign (separate design system effort in surface-design-phase1-4)
- New frontend rendering for new surface kinds (existing A2UI renderer already handles all component types)
- Changes to the Planner agent or PlanOutput contract
- Changes to approval or insight surface push paths (these are system-generated, not Presenter-driven)
- Internationalization of surface content

## Soul Alignment Verification

| Soul Principle | How This Spec Addresses It |
|---|---|
| "Never interrupt without reason" (Law 3) | Rate limiting: 5 surfaces/min, 3 insights/30min |
| "Clutter is not power" | Surface cap of 20 with priority-weighted eviction |
| "Elegant instrument, not chaotic control room" | Presenter decides IF a surface is warranted (can decline via `should_surface: false`) |
| "Interfaces appear when needed, not by default" | Presenter prompt explicitly says: no surface for simple conversational replies |
| "Preserve clarity" (Law 4) | Detail tabs for all 12 kinds: every surface can be inspected |
| "High signal density with low cognitive noise" | Typed properties prevent broken/malformed components |
| "Only Presenter talks to the user" | Surface kind decision moves to Presenter (agent boundary alignment) |
| "Degrade gracefully" | Malformed SurfaceSpec → chat-only fallback, never error |
