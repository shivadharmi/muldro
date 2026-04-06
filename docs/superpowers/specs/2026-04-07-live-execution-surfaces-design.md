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
