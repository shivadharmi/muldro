# Surface Architecture Redesign

**Date:** 2026-04-20
**Status:** approved (brainstorm complete)
**Authors:** shivadharmi + claude

## Problem

Current surface emission is fragmented. A single TaskRun produces 3+ surface cards (execution, approval push, REST-polled approval). The insight surface renders empty because the frontend type definition drops the `insight_data` payload silently. The Trace tab displays zeros for input/output tokens and cost because `TaskRun.trace_id` is frequently null when the detail endpoint reads it. Surface kinds are not strictly validated. Composition is duplicated across `surface_builder.py`, `surface_detail_builders.py`, and `graph_executor`. There is no clear boundary between system-generated surfaces (which should expose detail APIs) and agent-generated surfaces (which carry their own content and need no API).

## Goals

- **One surface per run** — A single `run` surface composes plan, steps, approval, results, trace. Transitions in place across phases. Replaces separate `execution`/`approval`/`plan` cards for the same run.
- **System vs agent boundary** — System-managed surfaces expose detail APIs; agent-managed surfaces carry their own content inline and expose none.
- **Composable units** — A small library of validated A2UI builders (header, step list, approval card, trace metrics, etc.) composed into every surface. One source of truth.
- **Observability truth** — Trace tab shows real tokens/cost/duration. Per-step breakdown is visible.
- **Strict validation** — `SurfaceKind` is a closed enum. Empty `children[]` is a build-time error. Detail tab registry is typed.

## Non-goals

- Rewriting the Presenter prompt.
- Changing the TrustEngine approval matrix.
- Adding new surface kinds beyond `run`, `summary`, `message`.
- Live collaboration or multi-user surface sync.

## Architecture

### Surface categories

| Category | Kind | Owner | Detail API | Frontend path |
|---|---|---|---|---|
| System | `run` | `GraphExecutor` | `GET /v1/surfaces/{id}/detail/{tab}` | tab-fetch (steps/plan/events/trace) |
| System | `summary` | `GraphExecutor` on run completion | same | tab-fetch (brief read-only) |
| System | `briefing` | `BriefingService` | same | tab-fetch |
| System | `insight` | `PerceptionService` via `_push_insight_surface` | same (already exists) | `insight-surface.tsx` |
| System | `alert` | `NotifierService` | same | alert renderer |
| System | `recommendation` | `SurfaceService._build_recommendation_surfaces` | same | generic A2UI |
| Agent | `message` | `Presenter` (promotion gate) | none (`detail_config=None`) | renders `surface_data.sections` directly |

Enforcement: a surface with `detail_config` is a system surface; the frontend goes through the tab-fetch path. A surface without `detail_config` is an agent surface; the frontend renders embedded children only. The two paths are mutually exclusive.

### Composable units

New module `backend/src/ui/units.py`. Each function returns a validated `A2UIComponent` (Card or Section). These units are the only way to build surface fragments across the codebase.

```
run_header(run, phase)                -> header card (title, status badge, agent, progress)
plan_summary(plan)                    -> goal, reasoning, success criteria, priority, trigger type
step_list(steps, current_step=None)   -> ordered list with per-step status icon and summary
approval_card(approval)               -> risk, trust level, preview, approve/reject/edit buttons
results_summary(results)              -> final findings, artifacts produced
trace_metrics(trace)                  -> input_tokens, output_tokens, cost_usd, duration_ms, step_breakdown
insight_body(insight_data)            -> signal summary, relevance, goals, suggested actions
event_timeline(events)                -> event-type-colored timeline
```

Existing call sites in `surface_builder.py`, `surface_detail_builders.py`, and `graph_executor._emit_surface_update` migrate to these units. Duplicated inline builders are removed.

### The run surface lifecycle

```
GraphExecutor._execute_dag begins
   └─> emits 'run' surface (phase=plan_ready)
       children = [run_header, plan_summary, step_list(pending)]

Each step transition
   └─> same surface_id, same kind
       children rebuilt from units with current state

TrustEngine approval_required
   └─> same surface_id
       children = [run_header, plan_summary, step_list, approval_card(active)]

Approval resolved
   └─> same surface_id, phase=executing
       approval_card unit no longer included

Run completes
   └─> same surface_id, phase=completed
       children = [run_header(completed), results_summary, trace_metrics]
       archive the run surface (expires_at=now+30d, hidden from active feed)
       emit NEW 'summary' surface (kind=summary, minimal card for the workspace feed)
```

The `summary` surface is small (one-line headline + metric strip + "View full run" deep-link). It is the durable workspace feed entry. The archived `run` surface remains retrievable via history/detail endpoints.

### Ownership consolidation (deletions)

- `_push_workspace_surface` in `jarvis.py` — deleted. GraphExecutor owns the run surface.
- `SurfaceService._build_approval_surfaces` — deleted. Approvals are embedded in the run surface.
- `NotifierService.notify(type="approval_request")` surface push — deleted. Notifier may still send a transport-tier notification (telegram, email) but does NOT push a standalone workspace surface.
- `_push_presenter_surface` in `jarvis.py` — replaced by `message` kind with promotion gate.

### Message surface promotion gate

The Presenter agent always returns a chat response. A chat response becomes a workspace `message` surface ONLY when it meets the structural significance threshold:

- Contains ≥ 1 structural component (table, chart, metric, kanban, calendar, timeline), OR
- Contains ≥ 2 distinct sections (so multi-part analysis, not a one-liner), OR
- Explicitly flagged by the agent (e.g. via a dedicated tool parameter `promote_to_workspace=True`)

Plain-text replies bypass the gate — they remain chat-only. This is structural, not semantic: the agent does not self-evaluate "usefulness," it just composes. The gate lives in a new helper `src/services/message_promotion.py`.

### Observability pipeline fixes

**Cause**: `TaskRun.trace_id` was set late (often after the first step), and `routes_history.py:382-386` returned hardcoded zeros when `run.trace_id` was null.

**Fix**:
1. In `GraphExecutor.execute()`, assign `TaskRun.trace_id` before the first step is scheduled. Use the `TraceManager` trace that already wraps the agent loop.
2. Add rollup columns to `TaskRun`: `input_tokens INT DEFAULT 0`, `output_tokens INT DEFAULT 0`, `cost_usd FLOAT DEFAULT 0.0`. Populated at run completion by summing the associated `Trace`.
3. Defensive fallback in `routes_history.py` detail endpoint: if `run.trace_id is None`, query `Trace` by `workspace_id+run_id` (Trace gets a `run_id` nullable column + index) before falling through to zeros.
4. Trace tab now shows per-step breakdown: each step's spans grouped together with cost/tokens/duration. `trace_metrics()` unit supports `detailed=True` mode.

### Strict validation

- `SurfaceKind` becomes a `StrEnum` in `contracts.py`. All surface builders accept only enum values; string literals are rejected at type-check time.
- `A2UISurface.children: list[A2UIComponent]` — empty list raises `ValueError` at construction time (the builder catches this and either populates or refuses to emit).
- `TAB_BUILDERS: dict[tuple[SurfaceKind, TabId], Callable[..., list[A2UIComponent]]]` — typed registry with fallback that raises on missing builders instead of silently returning empty.

### Frontend changes

- `frontend/src/lib/a2ui-types.ts` — add `insight_data?: InsightData | null` to `WorkspaceSurfacePush`. Add `SurfaceKind` enum with `run`, `summary`, `message`.
- `frontend/src/app/chat/page.tsx` — extract `insight_data` in `handleSurfacePush` and forward to store.
- `frontend/src/components/a2ui/components/run-surface.tsx` (new) — composes header/plan/steps/approval/results/trace from the surface children. Phase-aware.
- `frontend/src/components/workspace/surface-card.tsx` — add color entries for `run`, `summary`, `message`. Remove `execution`, `plan`, `approval` card variants (phased out).
- `frontend/src/components/workspace/surface-detail-modal.tsx` — `run` kind loads the 4 tabs (Steps / Plan / Events / Trace) via existing detail API.

## Data flow

```
User chat message
  -> JarvisOrchestrator.process_message_stream
  -> Planner emits PlanOutput
  -> GraphExecutor.execute(plan)
     -> creates TaskRun with trace_id set IMMEDIATELY
     -> emits 'run' surface (phase=plan_ready, composed from units)
     -> for each step:
          emits 'run' surface update (same surface_id)
          if approval_required: composes approval_card unit into children
          on step completion: units reflect new step status
     -> on final step:
          emits 'run' surface (phase=completed) with trace_metrics unit
          archives the run surface (expires_at=now+30d)
          emits 'summary' surface (durable workspace feed entry)
```

## Error handling

- Surface construction with empty children → raises, logs, skips push (never emits empty surface).
- Missing detail-tab builder → raises at registry lookup; frontend receives 404 for the tab.
- TraceManager finalize failure → logged but does not fail the run; rollup columns reflect what was recorded.
- Agent `message` promotion gate failure → treats as not-promoted (chat only); no crash.

## Testing

- Unit tests for each unit builder (input shapes, empty-case rejection, component type).
- Integration test: GraphExecutor run emits exactly 1 `run` surface + 1 `summary` on completion, with no sibling approval cards.
- Test: run with 1 approval produces 1 surface across the lifecycle (not 3).
- Test: TaskRun.trace_id is set before first step emits.
- Test: detail endpoint returns non-zero tokens/cost when Trace exists.
- Frontend test: insight surface renders signal summary + actions when backend sends `insight_data`.
- Frontend test: run-surface renders all phases correctly (plan_ready → executing → approval_needed → completed).
- Regression: no emission of `_push_workspace_surface`, `_build_approval_surfaces`, or notifier approval-card push paths.

## Implementation order

1. **Phase 1 — Foundation**: `ui/units.py`, SurfaceKind strict enum, empty-children rejection, unit tests.
2. **Phase 2 — Run surface**: GraphExecutor emits unified `run` surface from units; remove duplicate emission paths; emit `summary` on completion.
3. **Phase 3 — Observability**: early `trace_id` assignment, TaskRun rollup columns, defensive detail fallback, per-step trace breakdown.
4. **Phase 4 — Frontend**: insight_data wiring, `run-surface.tsx`, message promotion gate, updated kind color map.

## Migration notes

- No DB migration needed for surface data model; `UISurface.kind` already stores strings.
- New Alembic migration for TaskRun rollup columns and Trace.run_id.
- Old surfaces in DB (kind=`execution`, `plan`, `approval`) continue to render via fallback A2UIRenderer until they expire (24h TTL). No backfill.

## Open risks

- **Presenter surface replacement** — existing agents using `_push_presenter_surface` will break. Migration: route all through the `message` promotion gate. Plain-text Presenter replies lose their separate surface — acceptable since the chat panel already shows them.
- **Duplicate elimination** — removing `_build_approval_surfaces` means REST-only clients (no WebSocket) stop seeing pending approvals as standalone cards. Acceptable because the `run` surface is also REST-retrievable and embeds the approval.
