# Spec 3B: Execution Surface Frontend

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 3A (Execution Events Backend) — needs surface_update events flowing through Redis/WS
**Builds toward:** Spec 4B (Proactive Insight Surfaces) — reuses execution surface lifecycle

## Problem Statement

Spec 3A built the backend: surface_update events from GraphExecutor, InteractionLog, transport via Redis/WS. This spec builds the **frontend** — components that render live execution progress, inline approval cards, the WebSocket handler, and surface store updates.

## Design

### Component 1: WebSocket `surface_update` Handler

Update `use-jarvis-ws.ts`:

```typescript
// Add new message type handling alongside existing "surface"
} else if (msg.type === "surface_update" && onSurfaceUpdateRef.current) {
    onSurfaceUpdateRef.current(msg);
}
```

Add `onSurfaceUpdate` callback parameter to hook signature.

### Component 2: Surface Store Update Method

Add to `surface-store.ts`:

```typescript
updateSurface: (surfaceId: string, update: SurfaceUpdate) =>
    set((s) => {
        const idx = s.surfaces.findIndex((sf) => sf.id === surfaceId);
        if (idx === -1) return s; // Surface not found
        const next = [...s.surfaces];
        next[idx] = {
            ...next[idx],
            phase: update.phase,
            steps: update.steps,
            current_step: update.current_step,
            progress: update.progress,
            approval: update.approval,
            results: update.results,
        };
        return { surfaces: next };
    }),
```

### Component 3: Page Wiring

Wire `onSurfaceUpdate` in both pages:

```typescript
// In page.tsx and chat/page.tsx
const { sendAction } = useJarvisWs({
    userId: user?.user_id ?? "",
    onSurfacePush: handleSurfacePush,
    onSurfaceUpdate: (update) => updateSurface(update.surface_id, update),
    enabled: !!user,
});
```

Sort active executions (phase = executing/approval_needed) above completed surfaces in workspace grid.

### Component 4: TypeScript Types

Add to `a2ui-types.ts`:

```typescript
interface SurfaceUpdate {
    surface_id: string;
    phase: ExecutionPhase;
    steps: StepState[];
    current_step: string | null;
    progress: string;
    approval: ApprovalContext | null;
    results: ResultSummary | null;
}

type ExecutionPhase = "planning" | "plan_ready" | "executing" | "approval_needed" | "completed" | "failed" | "partial";

interface StepState {
    step_id: string;
    description: string;
    status: "pending" | "executing" | "completed" | "failed" | "approval_needed" | "user_action";
    output_summary: string | null;
    duration_ms: number | null;
}

interface ApprovalContext {
    approval_id: string;
    step_description: string;
    risk_reasoning: string;
    trust_context: string;
    graduation_hint: string;
}

interface ResultSummary {
    key_findings: string[];
    artifacts_created: string[];
    suggested_next: string[];
}
```

Add `ExecutionPhase` to `types/surfaces.ts`.

### Component 5: ExecutionSurface Component

New component rendering execution progress:

```tsx
function ExecutionSurface({ surface }) {
    const { phase, steps, approval, results } = surface;
    return (
        <SurfaceCard>
            <SurfaceHeader title={surface.goal} phase={phase} />
            {phase === "planning" && <PlanningSpinner />}
            {phase !== "planning" && <StepList steps={steps} currentStep={surface.current_step} />}
            {phase === "approval_needed" && approval && <InlineApprovalCard approval={approval} />}
            {phase === "completed" && results && <ResultsSummary results={results} />}
            {phase === "failed" && <FailureContext steps={steps} />}
            <ProgressBar completed={completedCount} total={totalCount} />
        </SurfaceCard>
    );
}
```

### Component 6: StepList Component

Renders plan steps with status indicators:

- `pending` → ○ empty circle
- `executing` → ◉ pulsing filled (animated)
- `completed` → ✓ green checkmark with output summary
- `failed` → ✗ red with error
- `approval_needed` → ⚠ yellow warning
- `user_action` → 👤 user badge with context

### Component 7: InlineApprovalCard Component

Approval card rendered inside execution surface (not separate notification):

```tsx
function InlineApprovalCard({ approval }) {
    return (
        <ApprovalBox>
            <RiskReasoning>{approval.risk_reasoning}</RiskReasoning>
            <TrustContext>{approval.trust_context}</TrustContext>
            {approval.graduation_hint && <GraduationHint>{approval.graduation_hint}</GraduationHint>}
            <ApproveButton onClick={() => approve(approval.approval_id)} />
            <EditButton onClick={() => edit(approval.approval_id)} />
            <RejectButton onClick={() => reject(approval.approval_id)} />
        </ApprovalBox>
    );
}
```

### Component 8: Surface Card Execution Rendering

Update `surface-card.tsx` to render execution phases when surface has `phase` field:
- Show step count and progress bar
- Phase-specific status dot colors (blue pulsing for executing, amber for approval, green for completed)
- Compact step list in card preview

### Component 9: Renderer Registration

Add `execution_surface` case to `renderer.tsx` switch dispatcher.

## Files Changed

### New Files (3 components)
- `frontend/src/components/a2ui/components/execution-surface.tsx`
- `frontend/src/components/a2ui/components/inline-approval.tsx`
- `frontend/src/components/a2ui/components/step-list.tsx`

### Modified Files (10)
- `frontend/src/hooks/use-jarvis-ws.ts` — Add `surface_update` handler + callback
- `frontend/src/stores/surface-store.ts` — Add `updateSurface()` method
- `frontend/src/app/page.tsx` — Wire `onSurfaceUpdate`, sort active executions first
- `frontend/src/app/chat/page.tsx` — Wire `onSurfaceUpdate`
- `frontend/src/lib/a2ui-types.ts` — Add SurfaceUpdate, StepState, ApprovalContext, ResultSummary types
- `frontend/src/lib/types/surfaces.ts` — Add ExecutionPhase type
- `frontend/src/components/workspace/surface-card.tsx` — Execution phase rendering
- `frontend/src/components/workspace/surface-detail-modal.tsx` — Handle live updates
- `frontend/src/components/a2ui/renderer.tsx` — Add execution_surface case

## Testing Strategy

- Component test: ExecutionSurface renders each phase correctly
- Component test: StepList shows correct icons for each status
- Component test: InlineApprovalCard renders with actions
- Integration: surface_update WebSocket message → store update → re-render
- E2E: plan execution → live surface progress visible in workspace
- E2E: approval needed → inline card appears → approve → execution resumes

## Success Criteria

1. Live step-by-step progress visible in workspace during execution
2. Inline approval card appears within execution context
3. Completed surfaces show key findings and suggested next steps
4. No flickering or duplicate surfaces during updates
5. Active executions sorted above completed surfaces

## Blast Radius

**Low — all frontend, all additive (new components + wiring).**

| File | Change | Risk |
|------|--------|------|
| `use-jarvis-ws.ts` | Add message handler + callback | **MEDIUM** — WebSocket wiring |
| `surface-store.ts` | Add merge method | **LOW** — additive |
| `page.tsx` / `chat/page.tsx` | Wire callback | **LOW** — additive |
| 3 new components | New files | **None** — no existing code touched |

### Total: ~13 files (10 modified, 3 new components)
