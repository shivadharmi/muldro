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

## Section 5: Actions & API Integration

### 5A: Rejection Reason in Confirmation Modal

**Discovery:** The backend already supports rejection reasons end-to-end:
- `ApprovalDecisionRequest.reason: str | None` schema exists (`routes_approvals.py`)
- `decision_reason` column on `Approval` model is populated on reject
- Reason is included in `RuntimeEvent` payload and audit logs
- WebSocket `_handle_reject()` bridges to REST `reject_action()` which accepts `reason`

**The frontend just never sends one.** The confirmation modal needs an optional text field.

**File:** `frontend/src/components/a2ui/components/inline-approval.tsx`

**Change to reject confirmation modal:**
- Add optional `<textarea>` below the confirmation text: "Optionally explain why:"
- Placeholder: "e.g., wrong recipients, needs review first"
- 2 lines tall, `text-xs`, `bg-surface-1 border border-b-secondary rounded-[var(--radius-md)]`
- State: `rejectReason: string` (default empty)
- On "Yes, Reject": `sendAction("reject", { id: approval.approval_id, reason: rejectReason || undefined })`
- Reason is optional — user can reject without explanation

**No backend changes needed** — the schema and storage already handle it.

### 5B: Insight Action Preview Field

**Discovery:** `SuggestedActionRef` has `description`, `capability`, `action_input` but no tooltip/preview field. Tooltip text is currently hardcoded on the frontend.

**Backend contract change:**

**File:** `backend/src/orchestrator/contracts.py` (SuggestedActionRef class, line 255)

```python
class SuggestedActionRef(BaseModel):
    description: str
    capability: str
    action_input: dict[str, Any] = Field(default_factory=dict)
    action_preview: str = ""    # NEW — tooltip text explaining what will happen
```

**Frontend type change:**

**File:** `frontend/src/lib/a2ui-types.ts` (SuggestedActionRef interface, line 91)

```typescript
interface SuggestedActionRef {
  description: string;
  capability: string;
  action_input: Record<string, unknown>;
  action_preview: string;    // NEW
}
```

**Backend population:** Where insight surfaces are built (in `_push_insight_surface()` in `jarvis.py`, or wherever `SuggestedActionRef` objects are constructed), populate `action_preview` based on capability type:
- Write capabilities: "Creates a task to {description}"
- Read capabilities: "Fetches {capability} data without taking action"
- Respond capabilities: "Generates a response about {description}"
- Fallback: "" (empty — frontend falls back to generic text)

**Frontend tooltip wiring:**
- If `action.action_preview` is non-empty → use it as tooltip text
- If empty → fallback to "Execute: {action.description}"
- Approval button tooltips remain static (approve/edit/reject have fixed meanings)

### 5C: Approval Expiration Frontend Handling

**Discovery:** Backend expiration is on-demand, not proactive:
- `DEFAULT_EXPIRY_HOURS = 24` in `approval_service.py`
- When user tries to act on expired approval → HTTP 410 Gone, status set to "expired"
- Scheduler `_tick_stuck_runs()` cancels runs with expired approvals
- No dedicated auto-expiration background job

**Frontend behavior when countdown reaches 0:**

**File:** `frontend/src/components/a2ui/components/inline-approval.tsx`

1. **Disable all action buttons** — set `disabled` on Approve, Edit, Reject when `remainingMs <= 0`
2. **Show "Expired" badge** — replace countdown with `text-j-error font-medium` "Expired" text
3. **Do NOT send an "expired" action** — the backend handles this on-demand (410 on next action attempt)
4. **Handle 410 response gracefully** — if user somehow clicks before UI updates:
   - The WebSocket action handler receives the 410 from the REST bridge
   - Frontend should show a toast/notification: "This approval has expired"
   - Update the surface to reflect expired state

**Error handling in ws-action-store:**

**File:** `frontend/src/stores/ws-action-store.ts` (or wherever action results are handled)

The `action_result` WebSocket message already has `status: "error"` + `error: string`. When the backend returns 410, the WebSocket bridge should send back:
```json
{"type": "action_result", "action": "approve", "status": "error", "error": "Approval has expired"}
```
The frontend can detect "expired" in the error message and update the surface phase accordingly.

### 5D: REST Surface Reconnect Compatibility

**Discovery:** `GET /v1/workspace/surfaces` returns persisted surfaces from the `ui_surfaces` table. Execution surfaces store `last_surface_update` as JSONB — the raw `SurfaceUpdate` payload.

**Impact:** When contracts widen (new fields on StepState/ApprovalContext), the persisted JSONB will include new fields automatically on new emissions. Old persisted surfaces (pre-widening) will have `null`/missing values for new fields.

**Frontend compatibility:** All new fields have defaults (`null`, `0`, `""`, `true`). The frontend must handle missing fields gracefully:
- `started_at: null` → no elapsed timer (already specified)
- `expires_at: null` → no countdown (already specified)
- `risk_level: ""` → no risk badge
- `trust_level: ""` → no trust badge
- `triggering_step_id: null` → no step highlight
- `action_preview: ""` → fallback to generic tooltip text

**No migration needed** — Pydantic `extra="ignore"` + default values handle backward compatibility. Old surfaces work with new frontend. New surfaces include enriched data.

---

## Updated Files Changed Summary

### Backend (5 files, was 4)

| File | Change |
|---|---|
| `backend/src/orchestrator/contracts.py` | Widen StepState (5 fields), ApprovalContext (8 fields), PolicyDecision (4 fields), SuggestedActionRef (+action_preview) |
| `backend/src/services/trust_engine.py` | Populate trust_level, effective_trust_level, approved_count, rejected_count on PolicyDecision |
| `backend/src/services/graph_executor.py` | Forward new fields at 5 StepState + 2 ApprovalContext construction sites |
| `backend/src/services/surface_detail_builders.py` | Update graduation hint builder to use enriched context (if applicable) |
| `backend/src/orchestrator/jarvis.py` | Populate action_preview on SuggestedActionRef in `_push_insight_surface()` |

### Frontend (8 files, was 7)

| File | Change |
|---|---|
| `frontend/src/lib/a2ui-types.ts` | Widen StepState, ApprovalContext, SuggestedActionRef interfaces |
| `frontend/src/components/a2ui/components/step-list.tsx` | Step grouping, elapsed timer, triggering step highlight |
| `frontend/src/components/a2ui/components/inline-approval.tsx` | Timeout countdown + expiration handling, risk/trust badges, evidence section, reject confirmation with reason field, visual connector |
| `frontend/src/components/a2ui/components/execution-surface.tsx` | Phase transition animations, pass triggeringStepId to StepList |
| `frontend/src/components/a2ui/components/insight-surface.tsx` | Action tooltips (dynamic from action_preview), dismiss confirmation |
| `frontend/src/components/ui/tooltip.tsx` | New CSS-only tooltip component (~40 lines) |
| `frontend/src/lib/design-tokens.ts` | Add `riskLevelColor()` helper if needed |
| `frontend/src/stores/ws-action-store.ts` | Handle 410 expired approval error gracefully (if not already) |

### Tests (updated)

| File | Coverage |
|---|---|
| `backend/tests/test_contracts.py` | Validate widened StepState/ApprovalContext/SuggestedActionRef serialization, defaults |
| `backend/tests/test_trust_engine.py` | Verify trust_level/effective_trust_level on PolicyDecision |
| `backend/tests/test_graph_executor.py` | Verify enriched SurfaceUpdate emissions at all 5+2 sites |

---

## Updated Vertical Slices

### Slice 1: Timing (Features 1 + 2)
**Backend:** Widen StepState contract + forward `started_at`, `completed_at`, `timeout_seconds`, `error`, `retry_count` at 5 emission sites
**Frontend:** Widen StepState type, step grouping in step-list.tsx, elapsed timer hook + pill badge

### Slice 2: Approval (Features 3 + 5 + 6 + 5A + 5C)
**Backend:** Widen ApprovalContext + PolicyDecision contracts, enrich TrustEngine.evaluate(), forward all approval fields at 2 emission sites
**Frontend:** Widen ApprovalContext type, timeout countdown + expiration disable, risk/trust badges, evidence expand, reject confirmation with reason field, dismiss confirmation, triggering step highlight + connector, 410 error handling

### Slice 3: Interaction (Features 4 + 7 + 5B)
**Backend:** Add action_preview to SuggestedActionRef, populate in insight surface builder
**Frontend:** New tooltip component, wire dynamic tooltips into insight actions + static tooltips on approval buttons, phase transition animation wrappers in execution-surface.tsx

---

## Non-Goals

- Backend auto-expiration background job (currently on-demand + scheduler health check — sufficient)
- Step retry UI (manual retry button) — future feature
- Approval editing flow (what happens after "Edit" click) — existing behavior unchanged
- Backend approval timeout configuration API — future feature
- Drag-and-drop step reordering — not applicable
