# Execution Surface Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build frontend components and wiring so live execution progress surfaces render in the workspace and chat pages via WebSocket `surface_update` messages.

**Architecture:** Spec 3A (already implemented) emits `SurfaceUpdate` events from `GraphExecutor` → Redis → WebSocket. This plan adds: (1) TypeScript types mirroring the backend `SurfaceUpdate`/`StepState`/`ApprovalContext`/`ResultSummary` contracts, (2) a new `surface_update` WebSocket message handler, (3) a Zustand store `updateSurface()` method that merges incremental updates, (4) three new A2UI components (`ExecutionSurface`, `StepList`, `InlineApprovalCard`), (5) renderer registration, (6) page wiring with active-execution-first sorting.

**Tech Stack:** Next.js 14, TypeScript, Tailwind CSS, Zustand, custom `useJarvisWs` hook

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `frontend/src/components/a2ui/components/step-list.tsx` | Step rows with status icons and optional output summary |
| Create | `frontend/src/components/a2ui/components/inline-approval.tsx` | Approval card with risk reasoning, trust context, approve/edit/reject buttons |
| Create | `frontend/src/components/a2ui/components/execution-surface.tsx` | Phase-aware execution progress: planning spinner, step list, approval, results |
| Modify | `frontend/src/lib/a2ui-types.ts` | Add `SurfaceUpdate`, `StepState`, `ApprovalContext`, `ResultSummary`, `ExecutionPhase` types + extend `JarvisMessage` union |
| Modify | `frontend/src/lib/types/surfaces.ts` | Add `"execution"` to `SurfaceKind` |
| Modify | `frontend/src/stores/surface-store.ts` | Add `updateSurface()` method + execution fields on `WorkspaceSurface` |
| Modify | `frontend/src/hooks/use-jarvis-ws.ts` | Handle `surface_update` message type, add `onSurfaceUpdate` callback |
| Modify | `frontend/src/app/page.tsx` | Wire `onSurfaceUpdate`, sort active executions first in `allSurfaces` |
| Modify | `frontend/src/app/chat/page.tsx` | Wire `onSurfaceUpdate`, sort active executions first |
| Modify | `frontend/src/components/workspace/surface-card.tsx` | Phase-specific status dots + compact step count for execution surfaces |
| Modify | `frontend/src/components/a2ui/renderer.tsx` | Add `ExecutionSurface` case to switch dispatcher |
| Modify | `frontend/src/components/workspace/surface-detail-modal.tsx` | Show live ExecutionSurface content when surface has `phase` field |

---

### Task 1: TypeScript Types

**Files:**
- Modify: `frontend/src/lib/a2ui-types.ts:96-112` (JarvisMessage union)
- Modify: `frontend/src/lib/types/surfaces.ts`

- [ ] **Step 1: Add execution types to `a2ui-types.ts`**

Add these types BEFORE the `JarvisMessage` union (after the `ActionResult` interface, around line 95):

```typescript
// ── Execution surface types ───────────────────────────────────

export type ExecutionPhase =
  | "planning"
  | "plan_ready"
  | "executing"
  | "approval_needed"
  | "completed"
  | "failed"
  | "partial";

export interface StepState {
  step_id: string;
  description: string;
  status: "pending" | "executing" | "completed" | "failed" | "approval_needed" | "user_action";
  output_summary: string | null;
  duration_ms: number | null;
}

export interface ApprovalContext {
  approval_id: string;
  step_description: string;
  risk_reasoning: string;
  trust_context: string;
  graduation_hint: string;
}

export interface ResultSummary {
  key_findings: string[];
  artifacts_created: string[];
  suggested_next: string[];
}

export interface SurfaceUpdate {
  surface_id: string;
  phase: ExecutionPhase;
  steps: StepState[];
  current_step: string | null;
  progress: string;
  approval: ApprovalContext | null;
  results: ResultSummary | null;
}
```

Then extend the `JarvisMessage` union — add this member after the `action_result` member:

```typescript
  | { type: "surface_update"; surface_id: string; phase: string; steps: StepState[]; current_step: string | null; progress: string; approval: ApprovalContext | null; results: ResultSummary | null }
```

- [ ] **Step 2: Add `"execution"` to `SurfaceKind`**

In `frontend/src/lib/types/surfaces.ts`, add `"execution"` to the union:

```typescript
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
  | "execution";
```

- [ ] **Step 3: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/a2ui-types.ts frontend/src/lib/types/surfaces.ts
git commit -m "feat(spec3b): add execution surface TypeScript types"
```

---

### Task 2: Surface Store `updateSurface()` Method

**Files:**
- Modify: `frontend/src/stores/surface-store.ts`

- [ ] **Step 1: Add execution fields to `WorkspaceSurface` interface**

Add these optional fields to the `WorkspaceSurface` interface (after `children?`):

```typescript
export interface WorkspaceSurface {
  id: string;
  kind: SurfaceKind;
  preview: SurfacePreview;
  detail_config: DetailConfig | null;
  source_run_id: string | null;
  response_preview: string | null;
  created_at: string;
  children?: A2UIComponent[];
  // Execution surface fields (populated by surface_update messages)
  phase?: ExecutionPhase;
  steps?: StepState[];
  current_step?: string | null;
  progress?: string;
  approval?: ApprovalContext | null;
  results?: ResultSummary | null;
}
```

Add these imports at the top of the file:

```typescript
import type { ExecutionPhase, StepState, ApprovalContext, ResultSummary } from "@/lib/a2ui-types";
```

- [ ] **Step 2: Add `updateSurface` to `SurfaceState` interface**

Add to the `SurfaceState` interface:

```typescript
  updateSurface: (surfaceId: string, update: SurfaceUpdate) => void;
```

And import `SurfaceUpdate`:

```typescript
import type { ExecutionPhase, StepState, ApprovalContext, ResultSummary, SurfaceUpdate } from "@/lib/a2ui-types";
```

- [ ] **Step 3: Implement `updateSurface` in the store**

Add this method inside the `create<SurfaceState>` call, after `setSurfaces`:

```typescript
  updateSurface: (surfaceId, update) =>
    set((s) => {
      const idx = s.surfaces.findIndex((sf) => sf.id === surfaceId);
      if (idx === -1) return s;
      const next = [...s.surfaces];
      next[idx] = {
        ...next[idx],
        phase: update.phase as ExecutionPhase,
        steps: update.steps,
        current_step: update.current_step,
        progress: update.progress,
        approval: update.approval,
        results: update.results,
      };
      return { surfaces: next };
    }),
```

- [ ] **Step 4: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/surface-store.ts
git commit -m "feat(spec3b): add updateSurface() to surface store"
```

---

### Task 3: WebSocket `surface_update` Handler

**Files:**
- Modify: `frontend/src/hooks/use-jarvis-ws.ts`

- [ ] **Step 1: Add `onSurfaceUpdate` to hook options**

Add `onSurfaceUpdate` callback to the `UseJarvisWsOptions` interface:

```typescript
interface UseJarvisWsOptions {
  userId: string;
  onSurfacePush?: (surface: WorkspaceSurfacePush) => void;
  onSurfaceUpdate?: (update: SurfaceUpdate) => void;
  onActionResult?: (result: ActionResult) => void;
  onNotification?: (msg: JarvisMessage) => void;
  enabled?: boolean;
}
```

Import `SurfaceUpdate` from a2ui-types:

```typescript
import type { ActionResult, JarvisMessage, SurfaceUpdate, WorkspaceSurfacePush } from "@/lib/a2ui-types";
```

- [ ] **Step 2: Add `onSurfaceUpdate` to the destructured params and ref pattern**

In the function signature, add `onSurfaceUpdate` to the destructured object:

```typescript
export function useJarvisWs({
  userId,
  onSurfacePush,
  onSurfaceUpdate,
  onActionResult,
  onNotification,
  enabled = true,
}: UseJarvisWsOptions) {
```

Add the ref + sync effect (right after the existing `onNotificationRef` block):

```typescript
  const onSurfaceUpdateRef = useRef(onSurfaceUpdate);
  useEffect(() => {
    onSurfaceUpdateRef.current = onSurfaceUpdate;
  }, [onSurfaceUpdate]);
```

- [ ] **Step 3: Add `surface_update` message handler**

In the `ws.onmessage` handler, add this `else if` block BEFORE the `heartbeat` check (after the `action_result` handler):

```typescript
        } else if (msg.type === "surface_update" && onSurfaceUpdateRef.current) {
          onSurfaceUpdateRef.current(msg as unknown as SurfaceUpdate);
```

**Why `as unknown as SurfaceUpdate`**: The `JarvisMessage` union includes `surface_update` as a flat message. The cast bridges the union discriminant to the `SurfaceUpdate` interface. This is safe because the fields are identical — both sourced from the backend `SurfaceUpdate` Pydantic model.

- [ ] **Step 4: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/use-jarvis-ws.ts
git commit -m "feat(spec3b): handle surface_update WebSocket messages"
```

---

### Task 4: StepList Component

**Files:**
- Create: `frontend/src/components/a2ui/components/step-list.tsx`

- [ ] **Step 1: Create the StepList component**

Create `frontend/src/components/a2ui/components/step-list.tsx`:

```tsx
"use client";

import type { StepState } from "@/lib/a2ui-types";

interface StepListProps {
  steps: StepState[];
  currentStep: string | null;
}

const statusIcon: Record<string, { icon: string; className: string }> = {
  pending: { icon: "○", className: "text-t-tertiary" },
  executing: { icon: "◉", className: "text-blue-400 animate-pulse" },
  completed: { icon: "✓", className: "text-green-400" },
  failed: { icon: "✗", className: "text-red-400" },
  approval_needed: { icon: "⚠", className: "text-amber-400" },
  user_action: { icon: "👤", className: "text-purple-400" },
};

export function StepList({ steps, currentStep }: StepListProps) {
  return (
    <div className="space-y-1">
      {steps.map((step) => {
        const isCurrent = step.step_id === currentStep;
        const { icon, className } = statusIcon[step.status] ?? statusIcon.pending;

        return (
          <div
            key={step.step_id}
            className={`flex items-start gap-2 py-1.5 px-2 rounded text-sm ${
              isCurrent ? "bg-surface-1" : ""
            }`}
          >
            <span className={`shrink-0 w-5 text-center ${className}`}>{icon}</span>
            <div className="flex-1 min-w-0">
              <span className={`${isCurrent ? "text-t-primary font-medium" : "text-t-secondary"}`}>
                {step.description}
              </span>
              {step.output_summary && step.status === "completed" && (
                <p className="text-xs text-t-tertiary mt-0.5 line-clamp-2">
                  {step.output_summary}
                </p>
              )}
              {step.status === "failed" && step.output_summary && (
                <p className="text-xs text-red-400 mt-0.5 line-clamp-2">
                  {step.output_summary}
                </p>
              )}
            </div>
            {step.duration_ms != null && step.status === "completed" && (
              <span className="text-[10px] text-t-tertiary shrink-0">
                {formatDuration(step.duration_ms)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Compact step list for surface card preview (shows counts, not full list). */
export function StepListCompact({ steps }: { steps: StepState[] }) {
  const completed = steps.filter((s) => s.status === "completed").length;
  const failed = steps.filter((s) => s.status === "failed").length;
  const total = steps.length;

  return (
    <div className="flex items-center gap-2 text-xs text-t-tertiary">
      <span>{completed}/{total} steps</span>
      {failed > 0 && <span className="text-red-400">{failed} failed</span>}
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/step-list.tsx
git commit -m "feat(spec3b): add StepList component with status icons"
```

---

### Task 5: InlineApprovalCard Component

**Files:**
- Create: `frontend/src/components/a2ui/components/inline-approval.tsx`

- [ ] **Step 1: Create the InlineApprovalCard component**

Create `frontend/src/components/a2ui/components/inline-approval.tsx`:

```tsx
"use client";

import { useCallback } from "react";
import type { ApprovalContext } from "@/lib/a2ui-types";
import { useWsActionStore } from "@/stores/ws-action-store";

interface InlineApprovalCardProps {
  approval: ApprovalContext;
}

export function InlineApprovalCard({ approval }: InlineApprovalCardProps) {
  const sendAction = useWsActionStore((s) => s.sendAction);

  const handleApprove = useCallback(() => {
    sendAction("approve", { approval_id: approval.approval_id });
  }, [sendAction, approval.approval_id]);

  const handleReject = useCallback(() => {
    sendAction("reject", { approval_id: approval.approval_id });
  }, [sendAction, approval.approval_id]);

  const handleEdit = useCallback(() => {
    sendAction("edit_before_approve", { approval_id: approval.approval_id });
  }, [sendAction, approval.approval_id]);

  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-amber-400">⚠</span>
        <span className="text-sm font-medium text-t-primary">Approval Required</span>
      </div>

      {/* Step description */}
      <p className="text-sm text-t-secondary">{approval.step_description}</p>

      {/* Risk reasoning */}
      <div className="rounded bg-surface-1 p-3 space-y-2">
        <p className="text-xs font-medium text-t-secondary">Risk Assessment</p>
        <p className="text-xs text-t-tertiary">{approval.risk_reasoning}</p>
      </div>

      {/* Trust context */}
      <div className="text-xs text-t-tertiary">
        <span className="font-medium text-t-secondary">Trust: </span>
        {approval.trust_context}
      </div>

      {/* Graduation hint */}
      {approval.graduation_hint && (
        <p className="text-xs text-blue-400/80 italic">
          {approval.graduation_hint}
        </p>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2 pt-1">
        <button
          type="button"
          onClick={handleApprove}
          className="px-3 py-1.5 text-xs font-medium rounded-md bg-green-600 text-white hover:bg-green-500 transition-colors"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={handleEdit}
          className="px-3 py-1.5 text-xs font-medium rounded-md border border-b-primary text-t-secondary hover:bg-surface-1 transition-colors"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={handleReject}
          className="px-3 py-1.5 text-xs font-medium rounded-md text-red-400 hover:bg-red-500/10 transition-colors"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/inline-approval.tsx
git commit -m "feat(spec3b): add InlineApprovalCard with approve/edit/reject actions"
```

---

### Task 6: ExecutionSurface Component

**Files:**
- Create: `frontend/src/components/a2ui/components/execution-surface.tsx`

- [ ] **Step 1: Create the ExecutionSurface component**

Create `frontend/src/components/a2ui/components/execution-surface.tsx`:

```tsx
"use client";

import { useMemo } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ExecutionPhase, StepState, ApprovalContext, ResultSummary } from "@/lib/a2ui-types";
import { StepList } from "./step-list";
import { InlineApprovalCard } from "./inline-approval";

interface Props {
  component: A2UIComponent;
}

/** Extract execution fields from component properties (set by backend or surface store merge). */
function useExecutionProps(properties: Record<string, unknown>) {
  return useMemo(() => ({
    goal: (properties.goal as string) ?? "Executing...",
    phase: (properties.phase as ExecutionPhase) ?? "planning",
    steps: (properties.steps as StepState[]) ?? [],
    currentStep: (properties.current_step as string) ?? null,
    progress: (properties.progress as string) ?? "",
    approval: (properties.approval as ApprovalContext) ?? null,
    results: (properties.results as ResultSummary) ?? null,
  }), [properties]);
}

const phaseLabel: Record<string, { text: string; className: string }> = {
  planning: { text: "Planning", className: "text-blue-400" },
  plan_ready: { text: "Plan Ready", className: "text-blue-400" },
  executing: { text: "Executing", className: "text-blue-400" },
  approval_needed: { text: "Approval Needed", className: "text-amber-400" },
  completed: { text: "Completed", className: "text-green-400" },
  failed: { text: "Failed", className: "text-red-400" },
  partial: { text: "Partially Completed", className: "text-amber-400" },
};

export function A2UIExecutionSurface({ component }: Props) {
  const { goal, phase, steps, currentStep, approval, results, progress } =
    useExecutionProps(component.properties);

  const completedCount = steps.filter((s) => s.status === "completed").length;
  const totalCount = steps.length;
  const progressPct = totalCount > 0 ? completedCount / totalCount : 0;
  const label = phaseLabel[phase] ?? phaseLabel.planning;

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-t-primary">{goal}</h3>
        <span className={`text-xs font-medium ${label.className}`}>{label.text}</span>
      </div>

      {/* Planning spinner */}
      {phase === "planning" && (
        <div className="flex items-center gap-2 py-4 justify-center">
          <div className="w-4 h-4 border-2 border-blue-400/30 border-t-blue-400 rounded-full animate-spin" />
          <span className="text-xs text-t-tertiary">Analyzing and building plan...</span>
        </div>
      )}

      {/* Step list (shown for all phases except planning) */}
      {phase !== "planning" && steps.length > 0 && (
        <StepList steps={steps} currentStep={currentStep} />
      )}

      {/* Inline approval card */}
      {phase === "approval_needed" && approval && (
        <InlineApprovalCard approval={approval} />
      )}

      {/* Results summary */}
      {phase === "completed" && results && (
        <div className="space-y-2 rounded-lg bg-green-500/5 border border-green-500/20 p-3">
          {results.key_findings.length > 0 && (
            <div>
              <p className="text-xs font-medium text-t-secondary mb-1">Key Findings</p>
              <ul className="space-y-0.5">
                {results.key_findings.map((f, i) => (
                  <li key={i} className="text-xs text-t-tertiary flex items-start gap-1.5">
                    <span className="text-green-400 shrink-0">-</span>
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {results.artifacts_created.length > 0 && (
            <div>
              <p className="text-xs font-medium text-t-secondary mb-1">Artifacts</p>
              <div className="flex flex-wrap gap-1">
                {results.artifacts_created.map((a, i) => (
                  <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-t-secondary">
                    {a}
                  </span>
                ))}
              </div>
            </div>
          )}
          {results.suggested_next.length > 0 && (
            <div>
              <p className="text-xs font-medium text-t-secondary mb-1">Suggested Next</p>
              <ul className="space-y-0.5">
                {results.suggested_next.map((s, i) => (
                  <li key={i} className="text-xs text-t-tertiary flex items-start gap-1.5">
                    <span className="text-blue-400 shrink-0">→</span>
                    {s}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Failure context */}
      {phase === "failed" && (
        <div className="rounded-lg bg-red-500/5 border border-red-500/20 p-3">
          <p className="text-xs font-medium text-red-400 mb-1">Execution Failed</p>
          {steps.filter((s) => s.status === "failed").map((s) => (
            <p key={s.step_id} className="text-xs text-t-tertiary">
              <span className="text-red-400">✗</span> {s.description}
              {s.output_summary && `: ${s.output_summary}`}
            </p>
          ))}
        </div>
      )}

      {/* Progress bar */}
      {totalCount > 0 && (
        <div className="space-y-1">
          <div className="w-full h-1.5 bg-surface-2 rounded-full">
            <div
              className={`h-full rounded-full transition-all ${
                phase === "failed" ? "bg-red-500" : phase === "completed" ? "bg-green-500" : "bg-blue-500"
              }`}
              style={{ width: `${Math.min(progressPct * 100, 100)}%` }}
            />
          </div>
          {progress && (
            <p className="text-[10px] text-t-tertiary">{progress}</p>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/execution-surface.tsx
git commit -m "feat(spec3b): add ExecutionSurface component with phase rendering"
```

---

### Task 7: Renderer Registration

**Files:**
- Modify: `frontend/src/components/a2ui/renderer.tsx`

- [ ] **Step 1: Add ExecutionSurface import**

Add this import alongside the other component imports:

```typescript
import { A2UIExecutionSurface } from "./components/execution-surface";
```

- [ ] **Step 2: Add `ExecutionSurface` case to the switch**

In the `renderComponentInner` function's switch statement, add this case in the "Specialized" section (after the `Calendar` case):

```typescript
    case "ExecutionSurface":
      return <A2UIExecutionSurface key={component.id} component={component} />;
```

- [ ] **Step 3: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/a2ui/renderer.tsx
git commit -m "feat(spec3b): register ExecutionSurface in A2UI renderer"
```

---

### Task 8: Surface Card Execution Rendering

**Files:**
- Modify: `frontend/src/components/workspace/surface-card.tsx`

- [ ] **Step 1: Add execution-phase status dot colors**

Add this map after the existing `statusDotColor` map:

```typescript
const phaseDotColor: Record<string, string> = {
  planning: "bg-blue-400 animate-pulse",
  plan_ready: "bg-blue-400",
  executing: "bg-blue-400 animate-pulse",
  approval_needed: "bg-amber-400 animate-pulse",
  completed: "bg-green-400",
  failed: "bg-red-400",
  partial: "bg-amber-400",
};
```

Import `StepListCompact`:

```typescript
import { StepListCompact } from "@/components/a2ui/components/step-list";
```

- [ ] **Step 2: Render execution info in card body**

In the `SurfaceCard` component, add the execution-aware status dot and compact step list. Replace the status dot logic to prefer `phase` when present:

Change the status dot span to:

```tsx
        {(surface.phase || preview.status) && (
          <span
            className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${
              surface.phase
                ? phaseDotColor[surface.phase] ?? "bg-gray-400"
                : statusDotColor[preview.status!] ?? "bg-gray-400"
            }`}
          />
        )}
```

Add compact step list after the subtitle block (before the progress bar):

```tsx
      {/* Execution step count */}
      {surface.steps && surface.steps.length > 0 && (
        <div className="mb-2">
          <StepListCompact steps={surface.steps} />
        </div>
      )}
```

- [ ] **Step 3: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/workspace/surface-card.tsx
git commit -m "feat(spec3b): add execution phase rendering to surface cards"
```

---

### Task 9: Page Wiring — Workspace Page

**Files:**
- Modify: `frontend/src/app/page.tsx`

- [ ] **Step 1: Wire `onSurfaceUpdate` and add sorting**

Import `updateSurface` from the surface store. At the top of `WorkspacePage`, add:

```typescript
  const updateSurface = useSurfaceStore((s) => s.updateSurface);
```

Import `SurfaceUpdate`:

```typescript
import type { SurfaceUpdate } from "@/lib/a2ui-types";
```

Add the `onSurfaceUpdate` callback to the `useJarvisWs` call:

```typescript
  const { sendAction } = useJarvisWs({
    userId: user?.user_id ?? "",
    onSurfacePush: handleSurfacePush,
    onSurfaceUpdate: useCallback(
      (update: SurfaceUpdate) => updateSurface(update.surface_id, update),
      [updateSurface]
    ),
    enabled: !!user,
  });
```

- [ ] **Step 2: Sort active executions first**

Replace the `allSurfaces` memo with a version that sorts active executions to the top:

```typescript
  const allSurfaces = useMemo(() => {
    const map = new Map<string, WorkspaceSurface>();
    for (const s of restSurfaces) map.set(s.id, s);
    for (const s of wsSurfaces) map.set(s.id, s);
    const merged = Array.from(map.values());

    // Active executions first (executing or approval_needed), then by created_at desc
    const isActive = (s: WorkspaceSurface) =>
      s.phase === "executing" || s.phase === "approval_needed" || s.phase === "planning";
    return merged.sort((a, b) => {
      const aActive = isActive(a) ? 0 : 1;
      const bActive = isActive(b) ? 0 : 1;
      if (aActive !== bActive) return aActive - bActive;
      return b.created_at.localeCompare(a.created_at);
    });
  }, [restSurfaces, wsSurfaces]);
```

- [ ] **Step 3: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/page.tsx
git commit -m "feat(spec3b): wire surface_update + sort active executions in workspace"
```

---

### Task 10: Page Wiring — Chat Page

**Files:**
- Modify: `frontend/src/app/chat/page.tsx`

- [ ] **Step 1: Wire `onSurfaceUpdate`**

Import `updateSurface` from the surface store. Add near the other store selectors:

```typescript
  const updateSurface = useSurfaceStore((s) => s.updateSurface);
```

Import `SurfaceUpdate`:

```typescript
import type { SurfaceUpdate } from "@/lib/a2ui-types";
```

Add `onSurfaceUpdate` to the `useJarvisWs` call:

```typescript
  const { connected, sendAction } = useJarvisWs({
    userId,
    onSurfacePush: handleSurfacePush,
    onSurfaceUpdate: useCallback(
      (update: SurfaceUpdate) => updateSurface(update.surface_id, update),
      [updateSurface]
    ),
    enabled: !!user,
  });
```

- [ ] **Step 2: Sort active executions first in the surfaces panel**

Replace the surfaces rendering in the `surfaces` prop to sort active first:

```tsx
        surfaces={
          surfaces.length > 0 ? (
            <div className="p-3 space-y-3">
              <div className="flex items-center justify-between px-1">
                <span className="text-xs font-medium text-t-secondary">
                  Surfaces ({surfaces.length})
                </span>
              </div>

              {[...surfaces]
                .sort((a, b) => {
                  const isActive = (s: WorkspaceSurface) =>
                    s.phase === "executing" || s.phase === "approval_needed" || s.phase === "planning";
                  const aActive = isActive(a) ? 0 : 1;
                  const bActive = isActive(b) ? 0 : 1;
                  if (aActive !== bActive) return aActive - bActive;
                  return b.created_at.localeCompare(a.created_at);
                })
                .map((surface) => (
                  <SurfaceCard
                    key={surface.id}
                    surface={surface}
                    onClick={() => openDetailModal(surface.id)}
                  />
                ))}
            </div>
          ) : undefined
        }
```

- [ ] **Step 3: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/chat/page.tsx
git commit -m "feat(spec3b): wire surface_update + sort active executions in chat"
```

---

### Task 11: Surface Detail Modal Live Updates

**Files:**
- Modify: `frontend/src/components/workspace/surface-detail-modal.tsx`

- [ ] **Step 1: Render ExecutionSurface content in detail modal**

Import `A2UIExecutionSurface`:

```typescript
import { A2UIExecutionSurface } from "@/components/a2ui/components/execution-surface";
```

In the modal content area, add an execution surface view when the surface has a `phase` field. Add this block BEFORE the existing `{!loading && !error && !activeData && tabs.length === 0 && (` block:

```tsx
          {/* Live execution surface */}
          {surface.phase && (
            <A2UIExecutionSurface
              component={{
                type: "ExecutionSurface",
                id: `exec-${surface.id}`,
                properties: {
                  goal: surface.preview.title,
                  phase: surface.phase,
                  steps: surface.steps ?? [],
                  current_step: surface.current_step ?? null,
                  progress: surface.progress ?? "",
                  approval: surface.approval ?? null,
                  results: surface.results ?? null,
                },
                children: [],
                actions: [],
              }}
            />
          )}
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/workspace/surface-detail-modal.tsx
git commit -m "feat(spec3b): show live execution in surface detail modal"
```

---

### Task 12: Integration Verification

- [ ] **Step 1: Full type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors across all modified files

- [ ] **Step 2: Lint check**

Run: `cd frontend && npm run lint`
Expected: No lint errors

- [ ] **Step 3: Build check**

Run: `cd frontend && npm run build`
Expected: Successful production build

- [ ] **Step 4: Manual E2E verification (requires running backend)**

Start backend: `cd backend && source .venv/bin/activate && python run.py --worker`
Start frontend: `cd frontend && npm run dev`

Test flow:
1. Open workspace page — verify surfaces load
2. Trigger a plan execution via chat — verify a surface appears with "Planning" phase
3. Verify step list populates as execution progresses
4. If approval gate triggers — verify InlineApprovalCard renders inside the surface
5. After completion — verify results summary with key findings and suggested next
6. Verify active executions sort above completed surfaces
7. Click a surface card — verify detail modal shows live execution content

- [ ] **Step 5: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix(spec3b): integration fixes for execution surface frontend"
```
