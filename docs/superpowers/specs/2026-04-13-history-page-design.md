# History Page Design

## Status
Version: v1.0
Date: 2026-04-13
Purpose: Design spec for the History page — a top-level page showing all plans, runs, and steps with live execution monitoring.

## Overview

The History page is a "calm ledger" at `/history` — a chronological timeline of everything Jarvis has done, with live monitoring of active runs. Runs are the top-level grouping. Active runs auto-expand to show inline step status with real-time WebSocket updates. Completed runs collapse to a single row. Clicking any run opens a tabbed detail modal with full context.

### Design Principles (from soul.md)

- "High signal density with low visual and cognitive noise"
- "Information is layered so the user can go deeper only when useful"
- "The product should feel like an elegant instrument, not a chaotic control room"
- Active runs show enough to understand progress without clicking. Completed runs stay out of the way.

## API Contract

### `GET /v1/history` — List view

Paginated run list with embedded plan context, step summaries, and live execution state.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `status` | string | `"all"` | Filter: `all`, `executing`, `completed`, `failed`, `awaiting_approval`, `cancelled` |
| `source` | string | `"all"` | Filter: `all`, `background`, `user_message`, `schedule`, `event` |
| `search` | string | `""` | Full-text search across plan goal, step names, capabilities |
| `from` | ISO datetime | none | Start of date range |
| `to` | ISO datetime | none | End of date range |
| `limit` | int | `20` | Page size (max 50) |
| `offset` | int | `0` | Pagination offset |

**Response:**

```json
{
  "items": [
    {
      "run_id": "run_01ABC",
      "plan_id": "plan_01XYZ",
      "goal": "Send investor follow-up email",
      "source": "background",
      "trigger_type": "event",
      "status": "executing",
      "risk_level": "medium",
      "started_at": "2026-04-13T10:00:00Z",
      "completed_at": null,
      "error": null,
      "retry_count": 0,
      "step_count": 3,
      "completed_step_count": 1,
      "cost_usd": 0.0042,
      "steps": [
        {
          "step_id": "step_001",
          "name": "Search recent emails",
          "capability": "email.search",
          "status": "completed",
          "started_at": "2026-04-13T10:00:01Z",
          "completed_at": "2026-04-13T10:00:03Z"
        },
        {
          "step_id": "step_002",
          "name": "Draft follow-up",
          "capability": "email.draft",
          "status": "running",
          "started_at": "2026-04-13T10:00:04Z",
          "completed_at": null
        },
        {
          "step_id": "step_003",
          "name": "Send email",
          "capability": "email.send",
          "status": "pending",
          "started_at": null,
          "completed_at": null
        }
      ],
      "approval": null,
      "live_phase": "executing",
      "surface_id": "surf_01DEF"
    }
  ],
  "total": 47,
  "limit": 20,
  "offset": 0
}
```

**Key fields:**

- `steps[]` — always included, compact (no `output_data`). Used for inline step list on active runs and for step count/status on collapsed runs.
- `live_phase` — pulled from `UISurface.payload["last_surface_update"]` for active runs. `null` for completed/failed runs. Enables the first render to show current execution progress without waiting for a WS update.
- `approval` — if a step is `awaiting_approval`, the approval context is embedded inline so the InlineApprovalCard renders without a separate fetch. Shape: `{ approval_id, step_description, risk_level, trust_level }`.
- `cost_usd` — aggregated from token usage traces.
- `surface_id` — the linked `UISurface.surface_id` for active runs. `null` for completed/failed runs. The frontend uses this to map incoming WS `surface_update` messages (keyed by `surface_id`) to the correct run in the history store.
- `goal` — from the linked Plan record. Falls back to run context summary if no plan.

**Implementation notes:**

The endpoint joins `TaskRun` + `Plan` + `TaskStep` + `UISurface` (for live_phase) + `Approval` (for awaiting runs). For active runs, `live_phase` is read from `UISurface.payload.get("last_surface_update", {}).get("phase")` where `UISurface.source_run_id == run.run_id`. The `cost_usd` field is derived from the Traces table if `run.trace_id` exists, otherwise `null`.

### `GET /v1/history/{run_id}` — Detail view

Full context for the detail modal, including step output, artifacts, trace data, and runtime events.

**Response:**

```json
{
  "run_id": "run_01ABC",
  "plan": {
    "plan_id": "plan_01XYZ",
    "goal": "Send investor follow-up email",
    "reasoning_summary": "Detected unread investor email requiring follow-up...",
    "success_conditions": ["Email sent within 24h", "Includes updated deck"],
    "trigger_type": "event",
    "priority": "high"
  },
  "status": "completed",
  "source": "background",
  "started_at": "2026-04-13T10:00:00Z",
  "completed_at": "2026-04-13T10:00:18Z",
  "error": null,
  "steps": [
    {
      "step_id": "step_001",
      "name": "Search recent investor emails",
      "capability": "email.search",
      "status": "completed",
      "input_data": { "query": "from:investor@fund.com", "days": 7 },
      "output_data": { "result": "Found 3 threads from investor@fund.com..." },
      "started_at": "2026-04-13T10:00:01Z",
      "completed_at": "2026-04-13T10:00:03Z",
      "duration_ms": 2340,
      "error": null,
      "artifacts": []
    },
    {
      "step_id": "step_002",
      "name": "Draft follow-up reply",
      "capability": "email.draft",
      "status": "completed",
      "input_data": { "context": "Reply to investor follow-up questions" },
      "output_data": { "result": "Draft email content..." },
      "started_at": "2026-04-13T10:00:04Z",
      "completed_at": "2026-04-13T10:00:12Z",
      "duration_ms": 8700,
      "error": null,
      "artifacts": [
        { "artifact_id": "art_001", "title": "updated-deck-v2.pdf", "artifact_type": "attachment" }
      ]
    },
    {
      "step_id": "step_003",
      "name": "Send email",
      "capability": "email.send",
      "status": "completed",
      "input_data": { "to": "investor@fund.com" },
      "output_data": { "result": "Email sent successfully. Message ID: msg_abc123." },
      "started_at": "2026-04-13T10:00:13Z",
      "completed_at": "2026-04-13T10:00:20Z",
      "duration_ms": 7400,
      "error": null,
      "artifacts": []
    }
  ],
  "approvals": [
    {
      "approval_id": "apr_001",
      "step_id": "step_003",
      "status": "approved",
      "risk_level": "medium",
      "title": "Approve step: email.send",
      "decided_at": "2026-04-13T10:00:14Z",
      "decision_reason": null,
      "approved_by": "usr_01JTEST"
    }
  ],
  "trace": {
    "trace_id": "trace_001",
    "input_tokens": 4200,
    "output_tokens": 1800,
    "cost_usd": 0.0042,
    "duration_ms": 18400,
    "agents_invoked": ["perceiver", "planner", "operator", "presenter"],
    "tools_called": ["email_search", "email_draft", "email_send"]
  },
  "events": [
    { "event_type": "run_started", "occurred_at": "2026-04-13T10:00:00Z", "step_id": null, "payload": {} },
    { "event_type": "step_started", "occurred_at": "2026-04-13T10:00:01Z", "step_id": "step_001", "payload": {} },
    { "event_type": "tool_call_started", "occurred_at": "2026-04-13T10:00:01Z", "step_id": "step_001", "payload": { "tool_name": "email_search" } },
    { "event_type": "step_completed", "occurred_at": "2026-04-13T10:00:03Z", "step_id": "step_001", "payload": {} },
    { "event_type": "step_started", "occurred_at": "2026-04-13T10:00:04Z", "step_id": "step_002", "payload": {} },
    { "event_type": "step_completed", "occurred_at": "2026-04-13T10:00:12Z", "step_id": "step_002", "payload": {} },
    { "event_type": "step_started", "occurred_at": "2026-04-13T10:00:13Z", "step_id": "step_003", "payload": {} },
    { "event_type": "approval_requested", "occurred_at": "2026-04-13T10:00:13Z", "step_id": "step_003", "payload": { "risk_level": "medium" } },
    { "event_type": "approval_resolved", "occurred_at": "2026-04-13T10:00:14Z", "step_id": "step_003", "payload": { "decision": "approved" } },
    { "event_type": "step_completed", "occurred_at": "2026-04-13T10:00:20Z", "step_id": "step_003", "payload": {} },
    { "event_type": "run_completed", "occurred_at": "2026-04-13T10:00:20Z", "step_id": null, "payload": {} }
  ]
}
```

### Actions

| Endpoint | Method | Purpose | Notes |
|----------|--------|---------|-------|
| `/v1/runs/{run_id}/cancel` | POST | Cancel a running/paused run | Existing — keep as-is |
| `/v1/runs/{run_id}/resume` | POST | Resume a paused/awaiting run | Existing — keep as-is |
| `/v1/history/{run_id}/retry` | POST | Retry a failed/timed_out run | New — replaces unused `/v1/runs/{id}/retry` |

## Frontend Architecture

### Page structure

```
/history (new top-level page)
├── HistoryPage (page.tsx)
│   ├── HistoryFilters (status, source, date range, search)
│   ├── SummaryStats (active count, completed count, failed count, daily cost)
│   ├── HistoryTimeline (main content)
│   │   ├── RunRow (collapsed — single line per completed/failed run)
│   │   ├── RunRow [active] (auto-expanded — inline step list with live status)
│   │   └── RunRow [approval] (auto-expanded — inline step list + approval card)
│   └── LoadMore (pagination trigger)
└── RunDetailModal (drill-down on click)
    ├── Steps tab (default — full step output, artifacts, approval records)
    ├── Plan tab (goal, reasoning, success conditions)
    ├── Events tab (runtime event timeline with color-coded types)
    └── Trace tab (token metrics, cost, agents invoked, tools called)
```

### State management

- **`useHistoryStore`** — new Zustand store holding paginated run list, filter state, expanded run IDs, surface_id→run_id lookup map
- **React Query** — `useQuery` for initial fetch + pagination from `/v1/history`, detail fetches from `/v1/history/{run_id}` on modal open
- **WebSocket** — existing `useJarvisWs` hook, `onSurfaceUpdate` callback merges live step statuses into the history store via the surface_id→run_id lookup

### Live update flow

```
WebSocket surface_update message
  → useJarvisWs onSurfaceUpdate callback
  → useHistoryStore.updateRunLiveState(surface_id → run_id mapping)
  → RunRow re-renders with updated step statuses, phase, progress
```

The mapping from `surface_id` to `run_id` is built from the `source_run_id` field on `UISurface` records. The history list response includes `run_id`, and WS `surface_update` includes `surface_id`. The store maintains a lookup map populated during the initial fetch.

### Key behaviors

| Scenario | Behavior |
|----------|----------|
| Page loads | Fetch `GET /v1/history?limit=20`, render all runs |
| Active run exists | Auto-expanded with inline step list, live status icons |
| Step completes via WS | Icon updates (spinner → checkmark), progress bar advances |
| Approval needed via WS | InlineApprovalCard appears in the expanded run row |
| User approves | Existing WS action flow, step resumes, icons update live |
| Run completes | Row collapses to single line, status badge turns green |
| User clicks a run | RunDetailModal opens, fetches `GET /v1/history/{run_id}` |
| User filters | React Query refetch with new params, URL query string updated |
| Failed run retry | POST `/v1/history/{run_id}/retry`, run reappears as active |

## Visual Design

### Run row — collapsed (completed/failed)

```
[●] Send investor follow-up email
    Triggered by: email perception · today 10:02 AM · 3 steps · 18.4s · $0.004    [completed]
```

- Green/red dot for status
- Plan goal as title
- Subtitle: trigger source, time, step count, duration, cost
- Status badge (right-aligned)
- Failed runs show inline Retry button

### Run row — expanded (active)

```
[◉] Send investor follow-up email                                    [executing] 2/3 steps
    Triggered by: email perception · 2 min ago
    ┌─────────────────────────────────────────────────────────────┐
    │ ✓  Search recent investor emails         email.search  2.3s│
    │ ◉  Draft follow-up reply                 email.draft   ...  │  ← blue left border
    │ ○  Send email                            email.send    —   │  ← dimmed
    └─────────────────────────────────────────────────────────────┘
```

- Pulsing blue dot for active runs
- Steps shown inline: status icon, name, capability badge, duration/status
- Current step highlighted with left border accent
- Pending steps dimmed

### Run row — awaiting approval

Same as expanded, plus an inline approval card below the step list:

```
    ┌─ Approval required ─────────────────────────────────────────┐
    │ Create "Design sync" — Thu 2pm, 3 attendees · medium risk   │
    │                                          [Approve] [Reject] │
    └─────────────────────────────────────────────────────────────┘
```

### Status icons

| Status | Icon | Color |
|--------|------|-------|
| pending | ○ (open circle) | gray, dimmed |
| ready | ○ | gray |
| running | ◉ (filled circle) | blue, pulsing |
| completed | ✓ (checkmark) | green |
| failed | ✗ (cross) | red |
| waiting_approval | ■ (square) | yellow |
| skipped | — (dash) | gray |
| timed_out | ⏱ (clock) | orange |
| cancelled | ⊘ (null) | gray |

### Filter bar

```
[🔍 Search runs, plans, steps...]  [All Status ▾] [All Sources ▾] [Last 7 days ▾]
```

- Search: full-text across plan goal, step names, capabilities
- Status: dropdown — All, Executing, Completed, Failed, Awaiting Approval, Cancelled
- Source: dropdown — All, Background (perception), Chat, Schedule, Event
- Date range: preset picker — Last 24h, Last 7 days, Last 30 days, Custom range

### Summary stats bar

```
2 active · 14 completed today · 1 failed                                    $0.12 today
```

Compact stats below the filter bar. Updates via WS as runs complete.

### Detail modal tabs

**Steps tab (default):**
- Each step as a card: header (status icon, name, capability, duration) + expandable body (input_data, output_data, artifacts, approval record)
- Approval records shown as green/red badges: "Approved by you · today 10:02 AM · medium risk"
- Artifacts as clickable chips

**Plan tab:**
- Plan goal, reasoning_summary, success_conditions, priority, trigger_type
- Read from the `plan` field in the detail response

**Events tab:**
- Vertical timeline with dots and connecting line
- Color-coded event types: blue (started), purple (tool calls), yellow (approvals), green (completed), red (failed)
- Timestamps with millisecond precision
- Each event shows: timestamp, event_type, step name, relevant payload data

**Trace tab:**
- 4 metric cards: Input Tokens, Output Tokens, Cost, Duration
- Agents invoked: blue pill badges
- Tools called: gray pill badges

## File Changes

### Backend — New files

| File | Purpose |
|------|---------|
| `src/api/routes_history.py` | `GET /v1/history` (list), `GET /v1/history/{run_id}` (detail), `POST /v1/history/{run_id}/retry` |
| `src/api/schemas_history.py` | Pydantic response models: `HistoryListResponse`, `HistoryItemResponse`, `HistoryDetailResponse`, `HistoryStepResponse`, `HistoryTraceResponse`, `HistoryEventResponse` |

### Backend — Deleted files

| File | Reason |
|------|--------|
| `src/api/routes_runs.py` | All read endpoints replaced by history API. `cancel` and `resume` actions moved to `routes_history.py`. |

### Backend — Modified files

| File | Change |
|------|--------|
| `src/api/app.py` | Register `routes_history` router. Remove `routes_runs` router. Remove list endpoint from `routes_plans` (keep `GET /v1/plans/{id}` for internal tool use). |
| `src/api/routes_plans.py` | Remove `GET /v1/plans` (list) and `GET /v1/plans/{plan_id}/runs`. Keep `GET /v1/plans/{plan_id}` (used by internal tools). |

### Frontend — New files

| File | Purpose |
|------|---------|
| `src/app/history/page.tsx` | History page — data fetching, WS integration, layout |
| `src/stores/history-store.ts` | Zustand store — run list, filters, expanded IDs, live state |
| `src/components/history/run-row.tsx` | Run timeline row — collapsed + expanded + approval variants |
| `src/components/history/run-detail-modal.tsx` | Tabbed detail modal — Steps, Plan, Events, Trace |
| `src/components/history/history-filters.tsx` | Filter bar — search, status, source, date range |
| `src/components/history/step-card.tsx` | Step detail card for the modal Steps tab |
| `src/components/history/event-timeline.tsx` | Runtime event timeline for the modal Events tab |
| `src/components/history/trace-summary.tsx` | Token/cost/agent metrics for the modal Trace tab |

### Frontend — Modified files

| File | Change |
|------|--------|
| Navigation component | Add "History" nav item between Chat and Search |
| `next.config.js` | Add `/api/history/*` rewrite if not already covered by wildcard |

### Tests

| File | Purpose |
|------|---------|
| `tests/test_routes_history.py` | API contract tests: list pagination, filters, detail response shape, retry action, embedded live_phase, embedded approval |
| `tests/test_history_cleanup.py` | Verify removed endpoints return 404 (regression guard) |

## Removed Endpoints

The following endpoints are removed because they have zero callers in the codebase and are fully replaced by the history API:

| Endpoint | Former location |
|----------|----------------|
| `GET /v1/runs` | routes_runs.py |
| `GET /v1/runs/{run_id}` | routes_runs.py |
| `GET /v1/runs/{run_id}/steps` | routes_runs.py |
| `GET /v1/runs/{run_id}/trace` | routes_runs.py |
| `GET /v1/runs/{run_id}/checkpoints` | routes_runs.py |
| `GET /v1/runs/{run_id}/artifacts` | routes_runs.py |
| `POST /v1/runs/{run_id}/retry` | routes_runs.py |
| `GET /v1/plans` | routes_plans.py |
| `GET /v1/plans/{plan_id}/runs` | routes_plans.py |

### Kept endpoints

| Endpoint | Reason |
|----------|--------|
| `POST /v1/runs/{run_id}/cancel` | Used by approval rejection handler (routes_approvals.py). Moved to routes_history.py. |
| `POST /v1/runs/{run_id}/resume` | Used by scheduler for approval resume. Moved to routes_history.py. |
| `GET /v1/plans/{plan_id}` | Used by internal tools (intelligence_server.py agent reasoning). Stays in routes_plans.py. |
