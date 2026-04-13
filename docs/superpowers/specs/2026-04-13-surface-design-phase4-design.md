# Phase 4: Deep Interaction Redesigns

**Date:** 2026-04-13
**Branch:** `improve-surface-design-v1`
**Approach:** Feature-driven vertical slices (Timing Slice → Approval Slice → Interaction Slice)
**Scope:** Full stack — backend contract widening + frontend interaction features

## Context

Phases 1–3 established design tokens, page-level polish, and A2UI structural UX. Phase 4 adds the **interaction layer** — features requiring new state management and backend data forwarding.

**Key finding from data audit:** The backend already computes 14+ data points at each SurfaceUpdate emission point, but only forwards 5 fields on `StepState` and 5 on `ApprovalContext`. The bottleneck is contract schema, not data availability. Phase 4 widens the pipeline.

### Data Availability (all exist in backend, none forwarded to frontend)

| Data Point | Source | Model/Variable |
|---|---|---|
| `step.started_at` | TaskStep column | `s.started_at` |
| `step.completed_at` | TaskStep column | `s.completed_at` |
| `step.timeout_seconds` | TaskStep column | `s.timeout_seconds` |
| `step.error` | TaskStep column | `s.error` |
| `step.retry_count` | TaskStep column | `s.retry_count` |
| `approval.expires_at` | Approval column | `approval.expires_at` |
| `approval.risk_level` | Approval column | `approval.risk_level` |
| `risk.reversible` | RiskAssessment | `risk.reversible` |
| `risk.blast_radius` | RiskAssessment | `risk.blast_radius` |
| `state.trust_level` | TrustState | `state.trust_level` |
| `effective_trust_level` | TrustEngine.evaluate() | computed at line 100 |
| `ceiling.max_level` | TrustCeiling | `ceiling.max_level` |
| `state.approved_count` | TrustState | `state.approved_count` |
| `state.rejected_count` | TrustState | `state.rejected_count` |

### UI Visibility Tiering

- **Primary** (always visible, drives user decisions): `risk_level`, `trust_level`, `expires_at`, `started_at`/`duration_ms`, `triggering_step_id`, `graduation_hint`
- **Evidence** (available on demand via expand/details): `risk_reasoning`, `trust_context`, `reversible`, `blast_radius`, `effective_trust_level`, `approved_count`, `rejected_count`, `completed_at`, `timeout_seconds`, `error`, `retry_count`

---

## Section 1: Contract Widening

### StepState — 5 → 10 fields

**File:** `backend/src/orchestrator/contracts.py` (StepState class, line 283)
**Frontend mirror:** `frontend/src/lib/a2ui-types.ts` (StepState interface, line 128)

```python
class StepState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # ── Primary ──
    step_id: str
    description: str
    status: Literal["pending", "executing", "completed", "failed", "approval_needed", "user_action"]
    output_summary: str | None = None
    duration_ms: int | None = None
    started_at: str | None = None          # NEW — ISO 8601

    # ── Evidence ──
    completed_at: str | None = None        # NEW — ISO 8601
    timeout_seconds: int | None = None     # NEW
    error: dict | None = None              # NEW — {message, stack?, code?}
    retry_count: int | None = None         # NEW
```

**Frontend TypeScript:**

```typescript
interface StepState {
  step_id: string;
  description: string;
  status: "pending" | "executing" | "completed" | "failed" | "approval_needed" | "user_action";
  output_summary: string | null;
  duration_ms: number | null;
  started_at: string | null;          // NEW

  // Evidence
  completed_at: string | null;        // NEW
  timeout_seconds: number | null;     // NEW
  error: Record<string, unknown> | null;  // NEW
  retry_count: number | null;         // NEW
}
```

### ApprovalContext — 5 → 13 fields

**File:** `backend/src/orchestrator/contracts.py` (ApprovalContext class, line 295)
**Frontend mirror:** `frontend/src/lib/a2ui-types.ts` (ApprovalContext interface, line 136)

```python
class ApprovalContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # ── Primary ──
    approval_id: str
    step_description: str
    risk_level: str = ""                    # NEW
    trust_level: str = ""                   # NEW
    expires_at: str | None = None           # NEW — ISO 8601
    triggering_step_id: str | None = None   # NEW
    graduation_hint: str = ""

    # ── Evidence ──
    risk_reasoning: str                     # EXISTING — demoted to evidence tier
    trust_context: str                      # EXISTING — demoted to evidence tier
    reversible: bool = True                 # NEW
    blast_radius: str = "self"              # NEW
    effective_trust_level: str = ""         # NEW
    approved_count: int = 0                 # NEW
    rejected_count: int = 0                 # NEW
```

**Frontend TypeScript:**

```typescript
interface ApprovalContext {
  approval_id: string;
  step_description: string;
  risk_level: string;
  trust_level: string;
  expires_at: string | null;
  triggering_step_id: string | null;
  graduation_hint: string;

  // Evidence
  risk_reasoning: string;
  trust_context: string;
  reversible: boolean;
  blast_radius: string;
  effective_trust_level: string;
  approved_count: number;
  rejected_count: number;
}
```

### PolicyDecision — add trust fields

**File:** `backend/src/orchestrator/contracts.py` (PolicyDecision class, line 181)

Add 2 fields so `TrustEngine.evaluate()` can pass trust state back to callers:

```python
class PolicyDecision(BaseModel):
    # ... existing fields ...
    trust_level: str = ""                   # NEW — raw trust level
    effective_trust_level: str = ""         # NEW — after ceiling applied
```

### Backend Emission Changes

**TrustEngine.evaluate()** (`backend/src/services/trust_engine.py`, line 89):
- Populate `trust_level=state.trust_level` and `effective_trust_level=effective_level` on the returned `PolicyDecision`

**graph_executor._create_approval_and_pause()** (`backend/src/services/graph_executor.py`, line 939):
- Query TrustState for `approved_count`, `rejected_count` (state is already fetched inside TrustEngine, but we need it at the call site — either pass it through PolicyDecision or re-query; prefer passing through)
- Populate widened ApprovalContext from in-scope variables:
  - `risk.risk_level`, `risk.reversible`, `risk.blast_radius`
  - `decision.trust_level`, `decision.effective_trust_level`
  - `approval.expires_at` (ISO string)
  - `step.step_id` as `triggering_step_id`

**graph_executor StepState constructions** (5 locations at lines 356, 588, 634, 665, 1104):
- Forward `s.started_at` (ISO string), `s.completed_at` (ISO string), `s.timeout_seconds`, `s.error`, `s.retry_count` from the TaskStep model

**PolicyDecision trust counters:** Add `approved_count: int = 0` and `rejected_count: int = 0` to PolicyDecision. Populate in `TrustEngine.evaluate()` from the fetched TrustState.

### Frontend Surface Store

**File:** `frontend/src/stores/surface-store.ts`

The `updateSurface()` method already merges incoming SurfaceUpdate fields via spread. New fields on StepState and ApprovalContext flow through automatically — no store changes needed.

### WebSocket Message Type

**File:** `frontend/src/lib/a2ui-types.ts` (JarvisMessage surface_update variant, line 173)

The `surface_update` message type references `StepState[]` and `ApprovalContext | null`, which are the widened interfaces. No separate message type change needed.

---

## Section 2: Timing Slice (Features 1 + 2)

### Feature 1: Step Grouping for Long Lists

**File:** `frontend/src/components/a2ui/components/step-list.tsx`

**Behavior:**
- Threshold: **5+ completed steps** triggers grouping
- Completed steps collapse into a single summary row: "N steps completed · Xs total"
- Summary row shows: green check icon, count text, total duration (sum of `duration_ms`), expand toggle ("▸ Expand" / "▾ Collapse")
- Click to expand shows all completed steps in their normal rendering
- Executing, pending, failed, and approval_needed steps are always visible (never grouped)
- Animation: expanded steps use `animate-slide-in-up` class on mount

**State:**
- `showCompletedSteps: boolean` (default `false`) — single toggle since there's only one completed group
- Summary row uses `bg-j-success-soft border border-j-success/12 rounded-[var(--radius-md)]`

**Rendering logic:**
```
completedSteps = steps.filter(s => s.status === "completed")
shouldGroup = completedSteps.length >= 5 && !showCompletedSteps

// Iterate steps in original array order
for each step in steps:
  if step.status === "completed" && shouldGroup:
    if first completed step encountered:
      render <CompletedGroupSummary count, totalDuration, onExpand>
    else:
      skip (already represented by summary)
  else:
    render step normally

// When expanded (showCompletedSteps === true), all steps render in original order
```

**Order preservation:** Steps always render in their original array order. The summary row replaces the block of completed steps in-place (inserted at the position of the first completed step). Non-completed steps remain at their original positions. When expanded, all steps render normally — no reordering.

### Feature 2: Elapsed Time Indicators

**File:** `frontend/src/components/a2ui/components/step-list.tsx`

**Behavior:**
- Shows a live-updating timer on steps with `status === "executing"` and `started_at !== null`
- Ticks every 1 second via `useEffect` + `setInterval`
- Computed: `Math.floor((Date.now() - new Date(started_at).getTime()) / 1000)`
- Display format: uses existing `formatDuration()` (ms → "Xs" / "Xm Ys")
- Also shown on failed steps: static duration from `duration_ms` or computed from `started_at`/`completed_at`

**Timer UI:**
- Pill badge, right-aligned: `flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-variant-numeric: tabular-nums`
- Executing: `bg-j-primary/12 text-j-primary` with pulsing dot (5px circle, `animate-pulse-live`)
- Failed: `bg-j-error/12 text-j-error` with static text "Xs" (no pulse)

**Hook:**
```typescript
function useElapsedTimer(startedAt: string | null, active: boolean): number {
  // Returns elapsed milliseconds, updates every 1s when active
  // Cleans up interval on unmount or when active becomes false
}
```

**Cleanup:** The `setInterval` is cleared when the step transitions away from "executing" status (active becomes false), preventing leaked timers.

---

## Section 3: Approval Slice (Features 3 + 5 + 6)

### Feature 3: Approval Timeout Countdown

**File:** `frontend/src/components/a2ui/components/inline-approval.tsx`

**Behavior:**
- When `approval.expires_at` is non-null, show a countdown timer in the card header
- Position: right-aligned in the header row, opposite "Approval Required"
- Display: clock icon + "Xm Ys" in `text-j-warning` color
- Ticks every 1s via the same `useElapsedTimer` pattern (but counting down: `expires_at - now`)
- When expired: show "Expired" in `text-j-error`
- When `expires_at` is null: countdown section not rendered (graceful degradation)

**Urgency escalation:**
- `> 2 minutes`: normal `text-j-warning` color
- `≤ 2 minutes`: `text-j-error` color + pulsing text
- `≤ 0`: "Expired" static text

### Feature 5: Reject/Dismiss Confirmation Dialogs

**Files:**
- `frontend/src/components/a2ui/components/inline-approval.tsx`
- `frontend/src/components/a2ui/components/insight-surface.tsx`

**Approval reject confirmation:**
- Clicking "Reject" sets `showRejectConfirm: true` instead of calling `sendAction`
- Opens existing `Modal` component (size="sm") with:
  - Title: "Reject this action?"
  - Body: "This will cancel \"{step_description}\". The task will be marked as rejected."
  - Buttons: "Cancel" (secondary, closes modal) + "Yes, Reject" (destructive, calls `sendAction("reject", ...)`)
- Cancel returns to the approval card without side effects

**Insight dismiss confirmation:**
- Clicking "Dismiss" sets `showDismissConfirm: true`
- Opens `Modal` (size="sm") with:
  - Title: "Dismiss this insight?"
  - Body: "This insight will be removed from your workspace."
  - Buttons: "Cancel" + "Yes, Dismiss" (calls existing `dismissInsight()`)

**Approve does NOT confirm** — it's the positive action and should be frictionless.

### Feature 6: Visual Connection Between Approval Card and Triggering Step

**Files:**
- `frontend/src/components/a2ui/components/step-list.tsx`
- `frontend/src/components/a2ui/components/execution-surface.tsx`

**Behavior:**
- When an approval is active, the execution surface passes `triggeringStepId` to StepList
- StepList highlights the matching step with:
  - Left border: `border-l-2 border-l-j-warning` (warning color, matching approval card)
  - Background: `bg-j-warning-soft` (soft warning tint)
  - Badge: `text-[9px] px-1.5 py-0.5 rounded bg-j-warning/12 text-j-warning uppercase font-medium` showing "awaiting"
- A subtle connector line (1px vertical, `bg-j-warning/30`, 8px tall) bridges the step and the approval card below

**StepList prop addition:**
```typescript
interface StepListProps {
  steps: StepState[];
  currentStep: string | null;
  triggeringStepId?: string | null;  // NEW
}
```

**ExecutionSurface wiring:**
```tsx
<StepList
  steps={steps}
  currentStep={currentStep}
  triggeringStepId={approval?.triggering_step_id ?? null}
/>
```

---

## Section 4: Interaction Slice (Features 4 + 7)

### Feature 4: Action Preview Tooltips

**Files:**
- New: `frontend/src/components/ui/tooltip.tsx` (~40 lines)
- Modified: `frontend/src/components/a2ui/components/insight-surface.tsx`
- Modified: `frontend/src/components/a2ui/components/inline-approval.tsx`

**CSS-only tooltip component:**
```typescript
interface TooltipProps {
  text: string;
  children: React.ReactNode;
  position?: "top" | "bottom";  // default: "top"
}
```

**Implementation:** `position: relative` wrapper with `position: absolute` tooltip. Uses `opacity: 0` → `group-hover:opacity-100` with `transition-opacity duration-150 delay-300`. Max-width 280px with text wrapping. Arrow via CSS border trick.

**Tooltip text sources:**

Insight action buttons:
- Primary action: "Creates a task to {capability description}"
- Secondary actions: "{action.description}" (already descriptive)
- Dismiss: no tooltip

Approval buttons:
- Approve: "Jarvis will proceed with this action"
- Edit: "Review and modify before executing"
- Reject: "Cancel this action (opens confirmation)"

### Feature 7: Phase Transition Animations

**File:** `frontend/src/components/a2ui/components/execution-surface.tsx`

**Behavior:** When the execution surface transitions between phases, new content sections animate in using existing CSS classes from `globals.css`.

**Animation mapping:**

| Transition | New Content | Animation Class |
|---|---|---|
| `planning` → `executing` | Step list appears | `animate-slide-in-up` |
| `executing` → `approval_needed` | Approval card appears | `animate-slide-in-up` |
| `executing` → `completed` | Results box appears | `animate-fade-in` |
| `executing` → `failed` | Failure box appears | `animate-fade-in` |
| Any phase change | Phase label | `transition-colors duration-200` |

**Implementation:** Wrap each phase-conditional section in a `<div>` with the animation class. Use React `key={phase}` on the wrapper so phase changes trigger unmount/mount, which re-triggers the CSS animation. The phase label already renders as a `<span>` — add `transition-colors duration-200` to smooth color changes.

**No animation library needed.** All animations use existing `@keyframes` from `globals.css` (fade-in: 0.15s, slide-in-up: 0.18s).

---

## Files Changed Summary

### Backend (4 files)

| File | Change |
|---|---|
| `backend/src/orchestrator/contracts.py` | Widen StepState (5 fields), ApprovalContext (8 fields), PolicyDecision (4 fields) |
| `backend/src/services/trust_engine.py` | Populate trust_level, effective_trust_level, approved_count, rejected_count on PolicyDecision |
| `backend/src/services/graph_executor.py` | Forward new fields at 5 StepState + 2 ApprovalContext construction sites |
| `backend/src/services/surface_detail_builders.py` | Update graduation hint builder to use enriched context (if applicable) |

### Frontend (7 files)

| File | Change |
|---|---|
| `frontend/src/lib/a2ui-types.ts` | Widen StepState, ApprovalContext TypeScript interfaces |
| `frontend/src/components/a2ui/components/step-list.tsx` | Step grouping, elapsed timer, triggering step highlight |
| `frontend/src/components/a2ui/components/inline-approval.tsx` | Timeout countdown, risk/trust badges, evidence section, reject confirmation, visual connector |
| `frontend/src/components/a2ui/components/execution-surface.tsx` | Phase transition animations, pass triggeringStepId to StepList |
| `frontend/src/components/a2ui/components/insight-surface.tsx` | Action tooltips, dismiss confirmation |
| `frontend/src/components/ui/tooltip.tsx` | New CSS-only tooltip component (~40 lines) |
| `frontend/src/lib/design-tokens.ts` | Add `riskLevelColor()` helper if needed (maps risk_level → token color) |

### Tests

| File | Coverage |
|---|---|
| `backend/tests/test_contracts.py` | Validate widened StepState/ApprovalContext serialization, defaults |
| `backend/tests/test_trust_engine.py` | Verify trust_level/effective_trust_level on PolicyDecision |
| `backend/tests/test_graph_executor.py` | Verify enriched SurfaceUpdate emissions at all 5+2 sites |

---

## Vertical Slices (Implementation Order)

### Slice 1: Timing (Features 1 + 2)
**Backend:** Widen StepState contract + forward `started_at`, `completed_at`, `timeout_seconds`, `error`, `retry_count` at 5 emission sites
**Frontend:** Widen StepState type, step grouping in step-list.tsx, elapsed timer hook + pill badge

### Slice 2: Approval (Features 3 + 5 + 6)
**Backend:** Widen ApprovalContext + PolicyDecision contracts, enrich TrustEngine.evaluate(), forward all approval fields at 2 emission sites
**Frontend:** Widen ApprovalContext type, timeout countdown, risk/trust badges, evidence expand, reject/dismiss confirmation modals, triggering step highlight + connector

### Slice 3: Interaction (Features 4 + 7)
**Frontend only:** New tooltip component, wire into insight + approval buttons, phase transition animation wrappers in execution-surface.tsx

---

## Non-Goals

- Approval expiration enforcement (backend auto-expiring approvals) — separate concern
- Step retry UI (manual retry button) — future feature
- Approval editing flow (what happens after "Edit" click) — existing behavior unchanged
- Backend approval timeout configuration API — future feature
- Drag-and-drop step reordering — not applicable
