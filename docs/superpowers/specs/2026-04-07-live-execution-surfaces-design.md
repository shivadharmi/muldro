# Spec 3: Live Execution Surfaces

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1 (Intelligent Planner) — surfaces render capability-level steps from PlanOutput
**Builds toward:** Spec 4 (Perception) — proactive insight surfaces reuse the same surface lifecycle

## Problem Statement

The current system has two execution visibility problems:

1. **Execution is a black box.** When GraphExecutor runs a multi-step plan, the user sees one surface card pushed BEFORE execution starts, then silence until the Presenter generates a response. For operations that take 10-60 seconds (research, multi-step tasks), the user has no visibility into what's happening. Events are published to Redis (`jarvis:run_progress:{run_id}`) but nothing in the frontend subscribes to them.

2. **Dual execution tracking.** Every interaction — even a "hello" — creates a full `TaskRun` record via `_create_lightweight_run`. This creates confusion: the user's execution history mixes lightweight audit records with real plan executions. The lightweight run also bypasses `execution_state.py` for completion, creating an inconsistent state management path.

### Soul/Vision Alignment Issues

- **Soul Law #4:** "The user should be able to understand what Jarvis knows, what it is doing, what it proposes, and why" — violated by black-box execution
- **Vision Pillar #6:** "Dynamic Interface Generation — produce the right interface for the moment" — surfaces are static snapshots, not dynamic
- **Soul:** "information is layered so the user can go deeper only when useful" — no layering exists

## Design

### Core Principle

An execution surface is a **living document** that reflects the current state of plan execution. It transitions through phases: planning → ready → executing → approval → complete. Each phase renders differently. The surface ID stays the same throughout — it's the same card evolving, not new cards appearing.

### Component 1: Execution Surface Lifecycle

```
Phase 1: PLANNING
  Planner is thinking. Surface shows spinner + goal.
  
Phase 2: PLAN_READY
  Plan decomposed. Surface shows step list with ○ pending markers.
  For simple plans (1-2 steps), this phase may be skipped.

Phase 3: EXECUTING
  Steps running. Surface updates per step:
  ✓ completed steps (with output summary)
  ◉ currently executing step (with activity indicator)
  ○ pending steps (waiting)
  Progress: "2 of 4 steps complete"

Phase 4: APPROVAL_NEEDED
  Execution paused at an approval gate.
  Surface shows completed steps + inline approval card.
  Approval card includes: what, why (LLM risk reasoning), actions.

Phase 5: COMPLETED
  All steps done. Surface transforms into results view:
  Key findings, artifacts created, suggested next steps.

Phase 6: FAILED
  Step failed after retries. Surface shows what worked,
  what failed, and error context.

Phase 7: PARTIAL
  Some steps completed, some need user action.
  Surface shows completed results + user action steps.
```

### Component 2: Surface Update Events

The GraphExecutor emits surface updates at each state transition. These are NOT new surfaces — they update an existing surface by ID.

**New event type:** `surface_update`

```python
@dataclass
class SurfaceUpdate:
    surface_id: str          # Same ID throughout execution
    phase: str               # planning, plan_ready, executing, approval_needed, completed, failed
    steps: list[StepState]   # Current state of all steps
    current_step: str | None # step_id of actively executing step
    progress: str            # "2 of 4 steps complete"
    approval: ApprovalContext | None  # Present only in approval_needed phase
    results: ResultSummary | None     # Present only in completed phase

@dataclass
class StepState:
    step_id: str
    description: str
    status: str              # pending, executing, completed, failed, approval_needed, user_action
    output_summary: str | None  # Brief result (first 200 chars)
    duration_ms: int | None

@dataclass
class ApprovalContext:
    approval_id: str
    step_description: str
    risk_reasoning: str      # From LLM risk assessor (Spec 2)
    trust_context: str       # "First time" or "Similar to 4 approvals"
    graduation_hint: str     # "~6 more until auto-execute"
```

**Emission points in GraphExecutor:**

```python
# In _execute_dag():
async def _execute_dag(self, run, surface_id):
    # Emit PLAN_READY
    await self._emit_surface_update(surface_id, "plan_ready", steps=all_steps)
    
    while True:
        ready_steps = await self._get_ready_steps(run.run_id)
        
        for step in ready_steps:
            # Emit EXECUTING (step starting)
            await self._emit_surface_update(surface_id, "executing", current_step=step.step_id)
            
            # ... approval gate check (Spec 2) ...
            if needs_approval:
                # Emit APPROVAL_NEEDED
                await self._emit_surface_update(surface_id, "approval_needed", approval=ctx)
                return
            
            await self._execute_step(run, step)
            
            # Emit EXECUTING (step completed)
            await self._emit_surface_update(surface_id, "executing", current_step=None)
        
        if all_complete:
            # Emit COMPLETED
            await self._emit_surface_update(surface_id, "completed", results=summary)
            break
```

### Component 3: Surface Transport

Surface updates flow through the existing Redis → WebSocket pipeline:

```
GraphExecutor
    → _emit_surface_update(surface_id, phase, ...)
    → Serialize to SurfaceUpdate
    → Publish to Redis channel: jarvis:a2ui:{user_id}
    → WebSocket delivers to frontend
    → useSurfaceStore updates surface by ID
    → React re-renders the surface card
```

**Key:** The `surface_id` is created when the plan is generated (Phase 1), before execution starts. All subsequent updates reference this same ID. The frontend store merges updates into the existing surface — no new cards appear.

**Redis message format:**
```json
{
  "type": "surface_update",
  "surface_id": "surf_01HXYZ...",
  "phase": "executing",
  "data": {
    "steps": [...],
    "current_step": "step_3",
    "progress": "2 of 4 steps complete"
  }
}
```

### Component 4: Frontend Execution Surface Component

New React component: `ExecutionSurface` — renders a plan's execution state.

**Rendering by phase:**

```tsx
function ExecutionSurface({ surface }: { surface: ExecutionSurfaceData }) {
  const { phase, steps, approval, results } = surface;

  return (
    <SurfaceCard>
      <SurfaceHeader title={surface.goal} phase={phase} />
      
      {phase === "planning" && <PlanningSpinner />}
      
      {phase !== "planning" && (
        <StepList steps={steps} currentStep={surface.current_step} />
      )}
      
      {phase === "approval_needed" && approval && (
        <InlineApprovalCard approval={approval} />
      )}
      
      {phase === "completed" && results && (
        <ResultsSummary results={results} />
      )}
      
      {phase === "failed" && (
        <FailureContext steps={steps} />
      )}
      
      <ProgressBar completed={completedCount} total={totalCount} />
    </SurfaceCard>
  );
}
```

**StepList component:**
```tsx
function StepList({ steps, currentStep }) {
  return (
    <div>
      {steps.map(step => (
        <StepRow key={step.step_id}>
          <StepIcon status={step.status} isActive={step.step_id === currentStep} />
          <StepDescription>{step.description}</StepDescription>
          {step.status === "completed" && step.output_summary && (
            <StepOutput>{step.output_summary}</StepOutput>
          )}
          {step.status === "user_action" && (
            <UserActionBadge>{step.user_context}</UserActionBadge>
          )}
        </StepRow>
      ))}
    </div>
  );
}
```

**Step icons by status:**
- `pending` → ○ (empty circle)
- `executing` → ◉ (pulsing filled circle, animated)
- `completed` → ✓ (checkmark, green)
- `failed` → ✗ (red)
- `approval_needed` → ⚠ (yellow warning)
- `user_action` → 👤 (user indicator)

### Component 5: Inline Approval

When execution pauses for approval, the approval card appears INSIDE the execution surface, not as a separate notification.

```tsx
function InlineApprovalCard({ approval }: { approval: ApprovalContext }) {
  return (
    <ApprovalBox>
      <ApprovalTitle>Approval needed</ApprovalTitle>
      <ApprovalDescription>{approval.step_description}</ApprovalDescription>
      
      <RiskReasoning>
        {approval.risk_reasoning}
      </RiskReasoning>
      
      <TrustContext>
        {approval.trust_context}
        {approval.graduation_hint && (
          <GraduationHint>{approval.graduation_hint}</GraduationHint>
        )}
      </TrustContext>
      
      <ApprovalActions>
        <ApproveButton onClick={() => approve(approval.approval_id)} />
        <EditButton onClick={() => edit(approval.approval_id)} />
        <RejectButton onClick={() => reject(approval.approval_id)} />
      </ApprovalActions>
    </ApprovalBox>
  );
}
```

The user sees exactly where in the plan execution paused, why it needs approval, and what happens next. Context is preserved — no separate notification to hunt for.

### Component 6: Replace Lightweight TaskRun with InteractionLog

The lightweight TaskRun serves an audit purpose — tracking every interaction. But it doesn't need the full TaskRun/TaskStep/state-machine machinery.

**New model:**
```python
class InteractionLog(Base):
    """Lightweight audit record for every user interaction."""

    __tablename__ = "interaction_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    interaction_id: Mapped[str] = mapped_column(String, unique=True)  # ilog_ULID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    trace_id: Mapped[str] = mapped_column(String, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)

    message_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plan_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)  # goal + reasoning
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)  # if a plan was created
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)   # if execution happened

    intent: Mapped[str | None] = mapped_column(String, nullable=True)   # fast intent classification
    response_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)

    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    latency_ms: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

**Delete:** `_create_lightweight_run()` and `_complete_lightweight_run()` methods in jarvis.py. Replace with `_log_interaction()` that creates a single `InteractionLog` record — no state machine, no TaskStep, no checkpoint.

**TaskRun is reserved exclusively for plan execution** via GraphExecutor.

### Component 7: Surface Persistence

Execution surfaces are persisted to the `ui_surfaces` table (already exists) with phase-aware payloads. On frontend reconnection, `SurfaceService.build_workspace_surfaces()` returns surfaces including active executions.

**Active execution surfaces** (phase != completed/failed) are sorted above static surfaces, since they need user attention.

**Completed surfaces** persist for 24 hours (existing TTL) as reference cards.

## Files Changed

### New Files
- `frontend/src/components/a2ui/components/execution-surface.tsx` — ExecutionSurface component
- `frontend/src/components/a2ui/components/inline-approval.tsx` — InlineApprovalCard
- `frontend/src/components/a2ui/components/step-list.tsx` — StepList + StepRow
- `src/models/interaction_log.py` — InteractionLog model
- Alembic migration for `interaction_logs` table

### Modified Files
- `src/services/graph_executor.py` — Emit surface updates at each phase transition
- `src/orchestrator/jarvis.py` — Replace `_create_lightweight_run`/`_complete_lightweight_run` with `_log_interaction`. Create execution surface at plan generation, pass surface_id to GraphExecutor.
- `src/services/surface_builder.py` — Include active execution surfaces in workspace build
- `src/ui/contracts.py` — Add `SurfaceUpdate` type, execution-specific surface types
- `frontend/src/components/a2ui/renderer.tsx` — Add ExecutionSurface to the renderer switch
- `frontend/src/stores/surface-store.ts` — Handle `surface_update` events (merge into existing surface)
- `frontend/src/app/page.tsx` — Sort active execution surfaces above static ones

### Deleted
- `_create_lightweight_run()` in jarvis.py
- `_complete_lightweight_run()` in jarvis.py

## Testing Strategy

- Unit tests for surface update serialization/deserialization
- Unit tests for phase transition logic (each phase → valid next phases)
- Unit tests for InteractionLog creation (covers all interaction types)
- Integration test: execute 3-step plan → verify 5+ surface updates emitted (plan_ready, step1, step2, step3, completed)
- Integration test: approval gate → verify surface enters approval_needed phase with context
- Integration test: frontend receives surface_update → verify store merges correctly
- E2E test: full plan execution visible in workspace as live-updating surface
- Regression test: simple interactions (greetings) create InteractionLog, not TaskRun

## Success Criteria

1. Users see real-time step-by-step progress during plan execution
2. Approval cards appear inline within the execution context
3. Completed surfaces show key results, not just a status label
4. Simple interactions don't create TaskRun records (only InteractionLog)
5. Active executions are visually prominent in the workspace
6. Surface updates are smooth (no flickering, no duplicate surfaces)

## Blast Radius

This spec touches both backend execution infrastructure and the full frontend surface rendering pipeline. The key risk is the chain from GraphExecutor → Redis → WebSocket → Surface Store → React rendering — if any link breaks, surface updates silently fail.

### Tier 1: CRITICAL — Execution tracking & surface lifecycle

| File | What changes | Why |
|------|-------------|-----|
| `src/orchestrator/jarvis.py` | Delete `_create_lightweight_run()` (4-5 call sites in `process_message` + `process_message_stream`), delete `_complete_lightweight_run()` (3 call sites). Replace with `_log_interaction()`. Refactor `_push_workspace_surface()` into `_create_initial_surface()` + surface_id passed to GraphExecutor | Core orchestrator — most call sites |
| `src/services/graph_executor.py` | Add `_emit_surface_update()` calls after each step transition (start, complete, approval, done). Accept `surface_id` parameter in `execute_run()` | Execution engine — where progress events originate |
| `src/models/task_graph.py` | `TaskRun` with `source='user_message'` is deprecated. Queries filtering by this source must be updated or removed | Data model for execution tracking |
| `frontend/src/hooks/use-jarvis-ws.ts` | Add `surface_update` message type handler alongside existing `surface` handler. Add `onSurfaceUpdate` callback parameter | **CRITICAL** — without this, surface_update events are silently dropped |

### Tier 2: HIGH — Transport & storage

| File | What changes | Why |
|------|-------------|-----|
| `src/orchestrator/contracts.py` | Add `SurfaceUpdateMessage` model alongside existing `WorkspaceSurfacePush` | New contract for incremental surface updates |
| `src/ui/contracts.py` | Add `SurfaceUpdate` type with phase, steps, progress fields | A2UI contract extension |
| `src/api/routes_ws.py` | Handle `surface_update` message type in WebSocket connection handler | WebSocket delivery |
| `src/services/surface_builder.py` | Include active execution surfaces (phase != completed) in `build_workspace_surfaces()`, sort above static surfaces | Workspace reconnection |
| `src/services/event_bus.py` | Add `surface_update` event type handler | Event publishing |
| `frontend/src/stores/surface-store.ts` | Verify `addSurface()` merge works for partial updates (already does `...spread`). May need explicit `updateSurfacePartial()` method | Surface state management |
| `frontend/src/app/page.tsx` | Add `onSurfaceUpdate` callback to `useJarvisWs()` hook call | Workspace page |
| `frontend/src/app/chat/page.tsx` | Add `onSurfaceUpdate` callback to `useJarvisWs()` hook call | Chat page |

### Tier 3: MEDIUM — Frontend components

| File | What changes | Why |
|------|-------------|-----|
| `frontend/src/components/workspace/surface-card.tsx` | Handle live updates — re-render when surface phase/steps change | Surface card rendering |
| `frontend/src/components/workspace/surface-detail-modal.tsx` | Handle detail_config updates during execution | Detail modal |
| `frontend/src/components/a2ui/renderer.tsx` | Add ExecutionSurface case to renderer switch | Component dispatch |
| `frontend/src/lib/a2ui-types.ts` | Add `SurfaceUpdate` TypeScript type, execution surface data types | Frontend types |
| `src/services/execution_state.py` | Verify `InteractionLog` doesn't need state machine (it shouldn't) | State management |
| `src/services/eviction_service.py` | Update cleanup queries — may query `source='user_message'` TaskRuns | Data cleanup |
| `src/api/routes_runs.py` | Update run listing to distinguish real runs from InteractionLog | Run API |

### Tier 4: Tests

| File | What changes | Why |
|------|-------------|-----|
| `tests/test_orchestrator.py` | Rewrite — currently tests `_create_lightweight_run` in `process_message` flow | Core orchestrator tests |
| `tests/test_acceptance.py` | Update lightweight run expectations → InteractionLog | Acceptance pipeline |
| `tests/test_execution_state.py` | Verify no InteractionLog state transitions expected | State machine tests |
| `tests/test_graph_executor.py` | Add surface_update emission assertions | Executor tests |
| E2E tests | Verify surface updates reach frontend | Frontend integration |

### Critical Interdependency Chains

**Chain 1: Lightweight run deletion (must do in order)**
```
Create InteractionLog model + migration
    ↓
Implement _log_interaction() in jarvis.py
    ↓
Replace ALL _create_lightweight_run call sites (4-5 locations)
    ↓
Replace ALL _complete_lightweight_run call sites (3 locations)
    ↓
Delete old methods
    ↓
Update routes_runs.py queries
    ↓
Update tests
```
**Risk:** Missing any call site → runtime AttributeError.

**Chain 2: Surface update pipeline (must complete entire chain)**
```
Add SurfaceUpdateMessage contract (backend)
    ↓
GraphExecutor emits surface_update events to Redis
    ↓
routes_ws.py relays surface_update messages to WebSocket
    ↓
use-jarvis-ws.ts receives surface_update and calls onSurfaceUpdate
    ↓
page.tsx/chat/page.tsx wires onSurfaceUpdate to surface store
    ↓
surface-store.ts merges update into existing surface
    ↓
surface-card.tsx re-renders with new state
```
**Risk:** Any missing link → surface_update events silently dropped with no error.

### Frontend Changes (Hard Replacement)

| File | What changes | Why |
|------|-------------|-----|
| `frontend/src/hooks/use-jarvis-ws.ts` | Add `surface_update` message type handler. Add `onSurfaceUpdate` callback parameter alongside existing `onSurfacePush`. Parse `{type: "surface_update", surface_id, phase, data}` messages. | WebSocket receives live surface updates |
| `frontend/src/stores/surface-store.ts` | Add `updateSurface(surfaceId, partialData)` method that merges update into existing surface without replacing it. Existing `addSurface()` already does spread merge but needs explicit partial update support for phase/steps/progress fields. | Surface state merge |
| `frontend/src/app/page.tsx` | Wire `onSurfaceUpdate` callback to surface store's `updateSurface()`. Sort surfaces: active executions (phase = executing/approval_needed) above completed surfaces. | Workspace homepage |
| `frontend/src/app/chat/page.tsx` | Same `onSurfaceUpdate` wiring. Surface rail shows execution progress inline. | Chat split-pane |
| `frontend/src/lib/a2ui-types.ts` | Add `SurfaceUpdateMessage` type: `{type: "surface_update", surface_id, phase, steps: StepState[], current_step, progress, approval: ApprovalContext \| null, results: ResultSummary \| null}`. Add `StepState`, `ApprovalContext`, `ResultSummary` types. | A2UI protocol extension |
| `frontend/src/lib/types/surfaces.ts` | Add `ExecutionPhase` type: `"planning" \| "plan_ready" \| "executing" \| "approval_needed" \| "completed" \| "failed" \| "partial"`. | Phase enum |
| `frontend/src/components/workspace/surface-card.tsx` | Render execution phase — show step list with status icons (○ pending, ◉ executing with pulse animation, ✓ completed, ✗ failed, ⚠ approval). Show progress bar. | Live execution rendering |
| `frontend/src/components/a2ui/components/execution-surface.tsx` | **NEW** — Full execution surface component with step list, inline approval, results summary. | Execution progress |
| `frontend/src/components/a2ui/components/inline-approval.tsx` | **NEW** — Approval card rendered inside execution surface. Shows risk reasoning, trust context, graduation hint, approve/edit/reject buttons. | Inline approval UX |
| `frontend/src/components/a2ui/components/step-list.tsx` | **NEW** — Renders list of plan steps with status indicators, output summaries, and user-action badges. | Step-by-step display |
| `frontend/src/components/a2ui/renderer.tsx` | Add `execution_surface` case to the component type switch dispatcher. | Component registry |

### API Contract Changes (Hard Replacement)

| Endpoint | What changes | Why |
|----------|-------------|-----|
| `WS /ws/{user_id}` | New message type: `{type: "surface_update", ...}` alongside existing `{type: "surface", ...}`. Backend publishes surface_update events from GraphExecutor through Redis to WS. | Live execution progress |
| `GET /v1/workspace/surfaces` | Response surfaces include `phase` field for active executions. Active surfaces sorted first. | Workspace reconnection |
| `GET /v1/runs/{id}` | Still returns `TaskRun` — but lightweight runs (source=user_message) no longer exist. Only real plan executions. | Cleaner run listing |

### Total: ~38 files affected (10 backend source, 5 tests, 14 frontend, 3 models/contracts, 3 new components, 3 API changes)
