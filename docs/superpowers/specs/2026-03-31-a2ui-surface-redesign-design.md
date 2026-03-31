# A2UI Surface Redesign — Rich Previews + Detail Modals

**Date**: 2026-03-31
**Status**: Draft
**Scope**: Backend surface generation, frontend rendering, data contracts, cleanup

## Problem

The current A2UI surface system has three issues:

1. **Thin surfaces for chat responses**: 14 decision types push surfaces, but most are just a heading + badge + truncated reasoning (200 chars). Decisions like `answer_directly`, `set_goal`, and `remember` add no value beyond what the chat already shows.

2. **No visual differentiation**: All surface cards look the same — same card wrapper, same structure. A plan card is indistinguishable from a briefing card or a research card.

3. **No drill-down**: Surfaces are static snapshots. The full grounded data (memories, entities, perception events, policy rules, execution traces) exists in the backend but never flows to the user. There is no mechanism to fetch detailed information on demand.

## Solution

A two-layer surface architecture:

- **Layer 1 — Rich Preview Cards**: Compact but information-dense cards in the workspace grid, visually differentiated by kind, with meaningful metrics, entity references, and progress indicators.

- **Layer 2 — Detail Modal**: A near-full-screen modal opened on card click, with tabbed layout and collapsible sections. Each tab lazy-fetches full grounded data from a server-specified API endpoint. The backend controls what tabs are available and what data they contain.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Which decisions push surfaces | 9 of 14 (remove 5 chat-only) | `answer_directly`, `set_goal`, `set_instruction`, `search_memory`, `remember` add no value as surfaces |
| Detail interaction model | Modal (not center pane) | Full grounded data needs near-full-screen space |
| Detail data organization | Tabs + collapsible sections | Tabs scale per surface kind; sections provide progressive disclosure |
| API strategy | Server-driven explicit endpoints | Backend embeds exact API paths per tab in `detail_config` — frontend is a pure renderer |
| Grid layout | CSS Grid `auto-fill` + `dense` | No empty space, responsive without hardcoded breakpoints |
| Backward compatibility | None | Dead code removed, no shims or legacy aliases |

## Surface Filtering

### Decisions That Push Surfaces

| Decision | Surface Kind | Rationale |
|----------|-------------|-----------|
| `create_task` | `plan` | Execution plan with tasks — core artifact |
| `draft_reply` | `recommendation` | Drafted email/message — needs user review |
| `recommend` | `recommendation` | Actionable recommendation |
| `research` | `summary` | Research results with sources |
| `summarize` | `summary` | Structured summary of information |
| `read_source` | `summary` | Observation from a data source |
| `observe` | `summary` | Background observation result |
| `add_to_brief` | `briefing` | Briefing content |
| `schedule_reminder` | `alert` | Scheduled reminder confirmation |

### Decisions Removed (Chat-Only)

- `answer_directly` — response is the chat message itself
- `set_goal` — confirmation already in chat
- `set_instruction` — confirmation already in chat
- `search_memory` — results already in chat response
- `remember` — confirmation already in chat

### Other Surface Sources (Enhanced, Not Filtered)

- **SurfaceService** (`build_workspace_surfaces`) — approvals, priorities, briefings, recommendations: enhanced with `preview` + `detail_config`
- **Notifier** (`_deliver_web`) — approval notifications: enhanced with `preview` + `detail_config`
- **Presenter tool** (`push_ui_update`) — unchanged, remains an escape hatch for agents

## Data Contracts

All models in `src/ui/contracts.py`.

### SurfaceMetric

Single metric displayed on a preview card.

```python
class SurfaceMetric(BaseModel):
    label: str
    value: str
    variant: Literal["default", "success", "warning", "danger"] = "default"
```

### SurfacePreview

Rich preview data for workspace grid cards.

```python
class SurfacePreview(BaseModel):
    title: str
    subtitle: str | None = None
    status: Literal[
        "pending", "running", "completed", "failed",
        "awaiting_approval", "cancelled"
    ] | None = None
    priority: Literal["low", "medium", "high", "critical"] | None = None
    metrics: list[SurfaceMetric] = []
    entities: list[str] = []
    progress: float | None = None       # 0.0 - 1.0
    timestamp: str | None = None        # ISO datetime
    tags: list[str] = []

    model_config = ConfigDict(extra="ignore")
```

### DetailTab

Single tab in the detail modal.

```python
class DetailTab(BaseModel):
    id: str
    label: str
    endpoint: str                       # Relative API path
    icon: str | None = None
    badge_count: int | None = None

    model_config = ConfigDict(extra="ignore")
```

### DetailConfig

Configuration for the detail modal.

```python
class DetailConfig(BaseModel):
    tabs: list[DetailTab]
    default_tab: str | None = None      # Defaults to first tab

    model_config = ConfigDict(extra="ignore")
```

### DetailSection

Collapsible section within a detail tab.

```python
class DetailSection(BaseModel):
    id: str
    title: str
    collapsed: bool = True
    children: list[A2UIComponent]

    model_config = ConfigDict(extra="ignore")
```

### DetailTabResponse

Response from a detail tab endpoint.

```python
class DetailTabResponse(BaseModel):
    tab_id: str
    sections: list[DetailSection]

    model_config = ConfigDict(extra="ignore")
```

### Redesigned WorkspaceSurfacePush

Replaces the old `WorkspaceSurfaceMetadata` entirely — no backward compatibility.

```python
class WorkspaceSurfacePush(BaseModel):
    id: str
    kind: SurfaceKind
    preview: SurfacePreview             # Rich card data for the grid
    detail_config: DetailConfig | None = None  # Modal tab configuration
    decision: str | None = None         # PlannerOutput decision type (from old metadata)
    source_run_id: str | None = None    # Linked TaskRun ID (from old metadata)
    response_preview: str | None = None # First 300 chars of agent response
    created_at: str
    ttl_hours: int = 24

    model_config = ConfigDict(extra="ignore")
```

Note: `children: list[A2UIComponent]` is removed. The frontend renders the grid card from `SurfacePreview` data via the `SurfaceCard` component — no A2UI tree needed for the grid. A2UI component trees are only used inside the detail modal (returned by the tab endpoints).

## Backend — Detail API

### Endpoint

```
GET /v1/surfaces/{surface_id}/detail/{tab_id}
```

Returns `DetailTabResponse`. Router file: `src/api/routes_surface_detail.py`.

### Flow

1. Look up surface from `ui_surfaces` table by `surface_id`
2. Read `kind` + stored metadata (`source_run_id`, `decision`, etc.)
3. Dispatch to tab builder based on `(kind, tab_id)`
4. Builder fetches grounded data from services, returns `DetailTabResponse` with A2UI component sections

### Tab Builder Registry

File: `src/services/surface_detail_builders.py`

```python
TAB_BUILDERS: dict[tuple[str, str], Callable] = {
    ("plan", "overview"):       build_plan_overview,
    ("plan", "context"):        build_plan_context,
    ("plan", "execution"):      build_plan_execution,
    ("summary", "overview"):    build_summary_overview,
    ("summary", "sources"):     build_summary_sources,
    ("summary", "context"):     build_summary_context,
    ("briefing", "priorities"): build_briefing_priorities,
    ("briefing", "events"):     build_briefing_events,
    ("briefing", "actions"):    build_briefing_actions,
    ("approval", "request"):    build_approval_request,
    ("approval", "risk"):       build_approval_risk,
    ("approval", "history"):    build_approval_history,
    ("recommendation", "overview"): build_recommendation_overview,
    ("recommendation", "context"):  build_recommendation_context,
    ("alert", "overview"):      build_alert_overview,
}
```

### Tab Data Sources

| Kind | Tab | Data Fetched |
|------|-----|-------------|
| plan | overview | TaskRun + TaskSteps (status, descriptions, durations) |
| plan | context | ContextPack — memories, entities, goals, preferences that influenced the plan |
| plan | execution | Step-by-step execution trace, tool calls, results, timings |
| summary | overview | Full response text, structured findings |
| summary | sources | Perception events, data source metadata, raw observations |
| summary | context | Memories + entities that informed the summary |
| briefing | priorities | Goal memories + pending runs + priority scoring |
| briefing | events | Recent perception events aggregated into the briefing |
| briefing | actions | Recommended actions with reasoning + linked entities |
| approval | request | Full tool call details (name, params, expected effect) |
| approval | risk | Governor policy decision, risk assessment, matching rules |
| approval | history | Past similar approvals/rejections for this tool/entity |
| recommendation | overview | Full recommendation text + supporting data |
| recommendation | context | Memories + entities + related past runs |
| alert | overview | Schedule details, trigger info, linked entities |

Builders use existing services: `MemoryService`, `WorldModel`, `ToolRegistry`, execution state queries. All render A2UI component trees via `renderer.py` builders.

## Backend — Enhanced Surface Builders

### `_push_workspace_surface()` in `jarvis.py`

Changes:
1. Remove 5 chat-only decisions from `surface_kind_map`
2. Build `SurfacePreview` with rich data extracted from `PlannerOutput` + execution context
3. Build `DetailConfig` with tabs via `build_detail_config(kind, surface_id)`
4. Build `children` via `render_preview_card(preview)` — replaces `_build_surface_children()`

### Preview Data Extraction Per Decision

| Decision | Preview Fields |
|----------|---------------|
| `create_task` | title=goal, status from run, metrics=[task count, priority], entities from context, progress from completed/total steps |
| `draft_reply` | title=goal, subtitle=recipient, metrics=[word count], entities=[email thread ref] |
| `recommend` | title=goal, metrics=[confidence], tags=[category] |
| `research` | title=goal, metrics=[source count, memory matches], entities=top entities found |
| `summarize` | title=goal, metrics=[source count], tags=[topic area] |
| `read_source` / `observe` | title=source name, status=observation status, metrics=[event count], timestamp |
| `add_to_brief` | title=briefing headline, metrics=[priority count, action count] |
| `schedule_reminder` | title=reminder description, timestamp=scheduled time, tags=["one-shot"] |

### Detail Config Factory

```python
def build_detail_config(kind: SurfaceKind, surface_id: str) -> DetailConfig:
    base = f"/v1/surfaces/{surface_id}/detail"
    TABS_BY_KIND: dict[str, list[DetailTab]] = {
        "plan": [
            DetailTab(id="overview", label="Overview", endpoint=f"{base}/overview"),
            DetailTab(id="context", label="Context", endpoint=f"{base}/context"),
            DetailTab(id="execution", label="Execution", endpoint=f"{base}/execution"),
        ],
        "summary": [
            DetailTab(id="overview", label="Overview", endpoint=f"{base}/overview"),
            DetailTab(id="sources", label="Sources", endpoint=f"{base}/sources"),
            DetailTab(id="context", label="Context", endpoint=f"{base}/context"),
        ],
        "briefing": [
            DetailTab(id="priorities", label="Priorities", endpoint=f"{base}/priorities"),
            DetailTab(id="events", label="Events", endpoint=f"{base}/events"),
            DetailTab(id="actions", label="Actions", endpoint=f"{base}/actions"),
        ],
        "approval": [
            DetailTab(id="request", label="Request", endpoint=f"{base}/request"),
            DetailTab(id="risk", label="Risk", endpoint=f"{base}/risk"),
            DetailTab(id="history", label="History", endpoint=f"{base}/history"),
        ],
        "recommendation": [
            DetailTab(id="overview", label="Overview", endpoint=f"{base}/overview"),
            DetailTab(id="context", label="Context", endpoint=f"{base}/context"),
        ],
        "alert": [
            DetailTab(id="overview", label="Overview", endpoint=f"{base}/overview"),
        ],
    }
    return DetailConfig(tabs=TABS_BY_KIND.get(kind, []))
```

### `SurfaceService.build_workspace_surfaces()`

Same enhancement — each surface gets `SurfacePreview` + `DetailConfig`:
- **Approval surfaces**: risk_level as status variant, tool name in subtitle, artifact count as metric
- **Priority surfaces**: run status, step progress, blocking reason
- **Briefing surfaces**: priority count, action count, headline
- **Recommendation surfaces**: severity, category tags

### `Notifier._deliver_web()`

Approval notification surfaces get `SurfacePreview` + `DetailConfig` matching SurfaceService-built approvals.

### Preview Card Renderer

New helper replaces `_build_surface_children()`:

```python
def render_preview_card(preview: SurfacePreview) -> list[A2UIComponent]:
    """Build A2UI component tree for a rich grid card from SurfacePreview data."""
```

Uses `renderer.py` builders: `heading()`, `badge()`, `text()`, `progress()`, `row()`, `card()`.

## Frontend — Rich Preview Card

### `SurfaceCard` Component

Renders `SurfacePreview` data in the workspace grid.

Card anatomy:
- **Header row**: Status dot (color-coded by status) + title + priority badge
- **Subtitle**: Context line (when `subtitle` is set)
- **Progress bar**: When `progress` is set (plans, executions)
- **Metrics row**: `SurfaceMetric[]` as label:value pairs
- **Entities row**: Pill-shaped tags (truncated to 3 with "+N more")
- **Footer**: Relative timestamp + click affordance icon

### Visual Differentiation by Kind

Left-border color per surface kind:
- `plan` — blue
- `approval` — amber
- `briefing` — green
- `summary` / `recommendation` — neutral gray
- `alert` — red

### Approval Card Behavior

Approval surfaces are unique — they require user action (approve/reject). The preview card in the grid shows the approval summary with risk badge but **no action buttons**. The approve/reject buttons live inside the detail modal's "Request" tab, where the user can see full context (tool details, risk assessment) before deciding. This prevents accidental approvals from the grid view.

## Frontend — Detail Modal

### `SurfaceDetailModal` Component

Modal opened on `SurfaceCard` click. Dimensions: `max-width: 1200px`, `max-height: 90vh`, centered with backdrop overlay. On screens narrower than 1200px, fills 95% width.

Modal anatomy:
- **Header**: Title + priority badge + close button (from `SurfacePreview`)
- **Tab bar**: Rendered from `DetailConfig.tabs`
- **Tab content**: Lazy-fetched from `DetailTab.endpoint` on tab click
- **Sections**: Rendered from `DetailTabResponse.sections` — collapsible via `DetailSection.collapsed`
- **Section content**: Standard `A2UIRenderer` for `children[]` — no new rendering logic

### Data Fetching

```
Tab clicked → check local cache → miss → fetch(tab.endpoint) → render DetailTabResponse
```

Tab responses cached in component state for modal lifetime. Closing the modal clears cache.

### API Client

New function: `fetchSurfaceDetail(surfaceId: string, tabId: string): Promise<DetailTabResponse>`

## Frontend — Grid Layout

### CSS Grid — No Empty Space

```css
.surface-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  grid-auto-flow: dense;
  gap: 16px;
}
```

- `auto-fill` + `minmax(340px, 1fr)` — responsive columns, no hardcoded breakpoints
- `grid-auto-flow: dense` — tight packing, no vertical holes
- `1fr` max — cards stretch to fill remaining space evenly
- Single surface fills full width; odd counts pack without trailing gaps

## Frontend — Wiring

- `WorkspaceCanvas` renders `SurfaceCard` for each surface (replaces raw `A2UIRenderer`)
- `SurfaceCard` `onClick` → `setActiveSurface(id)` + opens `SurfaceDetailModal`
- Modal reads `preview` + `detail_config` from store by `activeSurfaceId`
- WebSocket push format changes completely — old `children` + `metadata` shape is replaced by `preview` + `detail_config`. No backward compatibility with old format.

## Action System Fix

### Current State (Broken)

The A2UI action flow has three bugs:

1. **Backend only handles 3 actions**: `routes_ws.py` explicitly handles `approve`, `reject`, and `meeting_prep`. All other actions (reply, upload, custom agent actions) are silently dropped — no handler, no error response.

2. **`meeting_prep` is a stub**: Only logs the request, never executes.

3. **No error feedback**: When an action is unhandled or fails, the frontend receives no response. The user clicks a button and nothing visibly happens.

### Action Chain (How It Works Today)

```
Backend button builder: A2UIAction(type="click", payload={"action": "reply", "id": "..."})
  → Frontend button: onAction("click", {action: "reply", id: "..."})
  → action-handler.ts: sendAction("click", {action: "reply", ...})
  → WebSocket: { type: "action", payload: { action: "reply", ... } }  (spread overwrites "click")
  → Backend routes_ws.py: action = "reply" → NOT in approve/reject/meeting_prep → SILENTLY DROPPED
```

### Fix: Generic Action Router

Replace the hardcoded if/elif chain in `routes_ws.py` with a proper action dispatch system.

**Backend changes (`routes_ws.py`):**

1. Create an `ACTION_HANDLERS` registry mapping action names to handler functions:

```python
ACTION_HANDLERS: dict[str, Callable] = {
    "approve": handle_approve_action,
    "reject": handle_reject_action,
}
```

2. For actions not in the registry, route them through the orchestrator as a user command. This allows agent-defined button actions (reply, upload, prep card, etc.) to trigger Jarvis processing:

```python
async def _handle_action(user_id: str, action: str, payload: dict, app) -> dict:
    handler = ACTION_HANDLERS.get(action)
    if handler:
        return await handler(user_id, payload, app)
    # Generic: treat as a user command to the orchestrator
    return await _handle_orchestrator_action(user_id, action, payload, app)
```

3. `_handle_orchestrator_action` calls `process_message` with a synthesized message like `"[Action: {action}] {context from payload}"` so the Planner can route it to the right agent.

4. **Always send feedback** — every action gets an `action_result` response:

```python
await _broadcast(user_id, {
    "type": "action_result",
    "action": action,
    "status": "success" | "error",
    "result": result_data,
    "error": error_message,  # if status == "error"
})
```

**Frontend changes:**

1. **`action-handler.ts`**: Simplify to always use `sendAction(payload.action, payload)` — no special-casing for approve/reject. The backend handles routing.

```typescript
export function handleA2UIAction(
  sendAction: (action: string, payload: Record<string, unknown>) => void,
  _actionType: string,
  payload: Record<string, unknown>
) {
  const action = (payload.action as string) || _actionType;
  sendAction(action, payload);
}
```

2. **Action feedback UI**: Listen for `action_result` WebSocket messages and show toast/indicator:
   - `status: "success"` → brief success toast
   - `status: "error"` → error toast with message
   - Loading state on the button between click and result

3. **Button loading state**: Add `loading` support to `A2UIButton` — disable the button and show a spinner after click until `action_result` is received for that action.

### Detail Modal Actions

In the new modal design, actions (approve/reject, reply, etc.) live inside modal tab content. The same action system applies — buttons in modal sections use the same `onAction` → WebSocket → backend flow. The modal additionally:

- Shows action results inline (not just as toasts) for context
- Can refresh the current tab after a successful action (e.g., approval status changes)
- Passes `surface_id` in the action payload so the backend knows which surface the action relates to

## Dead Code Cleanup

### Backend Removals

- `_build_surface_children()` in `jarvis.py` — replaced by `render_preview_card()`
- `WorkspaceSurfaceMetadata` in `contracts.py` — fields absorbed into `SurfacePreview` + surface-level fields on `WorkspaceSurfacePush`
- 5 chat-only entries from `surface_kind_map`
- Ad-hoc card-building logic in `SurfaceService` that duplicates `render_preview_card()`
- `children` field on `WorkspaceSurfacePush` — grid cards rendered from `SurfacePreview`, not A2UI trees

### Backend Updates

- `push_ui_update()` tool in `communication_server.py` — update to produce `WorkspaceSurfacePush` in the new format (preview + detail_config, no children/metadata)

### Frontend Removals

- `CenterPaneSurface` component (`shell/center-pane-surface.tsx`) — replaced by `SurfaceDetailModal`
- `SurfaceDock` component (`shell/surface-dock.tsx`) — replaced by modal approach
- `SurfacePosition` type and `setPosition()` store action — no multi-position system
- Position-related logic (`workspace`, `right-pane`, `center-pane`, `inline`) in surface store
- Direct `A2UIRenderer` usage in `WorkspaceCanvas` for grid cards — replaced by `SurfaceCard`

### Store Simplification

Remove: `togglePin`, `setPosition`, `clearUnpinned`
Keep: `surfaces`, `activeSurfaceId`, `addSurface`, `removeSurface`, `setActiveSurface`

### Database Migration

- Add `preview` (JSONB) and `detail_config` (JSONB) columns to `ui_surfaces`
- Drop `metadata` column (useful fields moved to `preview` or surface-level columns)
- No data migration — existing surfaces expire via 24h TTL

## New Files

| File | Purpose |
|------|---------|
| `backend/src/api/routes_surface_detail.py` | Detail tab endpoint + dispatch |
| `backend/src/services/surface_detail_builders.py` | Tab builder functions (15 builders) |
| `frontend/src/components/workspace/surface-card.tsx` | Rich preview card component |
| `frontend/src/components/workspace/surface-detail-modal.tsx` | Detail modal with tabs + sections |

## Modified Files

| File | Changes |
|------|---------|
| `backend/src/ui/contracts.py` | Add 6 new Pydantic models |
| `backend/src/orchestrator/jarvis.py` | Rewrite `_push_workspace_surface`, remove `_build_surface_children`, filter 5 decisions |
| `backend/src/services/surface_builder.py` | Enhance all surface builders with `preview` + `detail_config` |
| `backend/src/services/notifier.py` | Enhance approval surface with `preview` + `detail_config` |
| `backend/src/ui/renderer.py` | Add `render_preview_card()` helper |
| `frontend/src/stores/surface-store.ts` | Simplify: remove position/pin logic, add modal state |
| `frontend/src/components/workspace/workspace-canvas.tsx` | Use `SurfaceCard` + new grid CSS |
| `frontend/src/app/page.tsx` | Remove center-pane/dock wiring, add modal |
| `frontend/src/lib/api.ts` | Add `fetchSurfaceDetail()` |
| `backend/src/api/routes_ws.py` | Replace hardcoded action if/elif with `ACTION_HANDLERS` registry + generic orchestrator fallback + always-send feedback |
| `frontend/src/components/a2ui/action-handler.ts` | Simplify to single codepath — always `sendAction(payload.action, payload)` |
| `frontend/src/components/a2ui/components/button.tsx` | Add loading state support (disable + spinner between click and action_result) |
| `frontend/src/hooks/use-jarvis-ws.ts` | Handle `action_result` messages — surface to UI as toasts or inline feedback |
| `backend/src/tools/communication_server.py` | Update `push_ui_update` tool to produce new `WorkspaceSurfacePush` format |
