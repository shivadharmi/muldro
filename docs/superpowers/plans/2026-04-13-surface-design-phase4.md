# Phase 4: Deep Interaction Redesigns — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen backend contracts to forward 14 already-available data points, then build 7 frontend interaction features (step grouping, elapsed timers, approval enrichment, tooltips, confirmations, visual connections, phase animations) across 3 vertical slices.

**Architecture:** Feature-driven vertical slices — each slice widens the specific contracts it needs AND implements the frontend feature. Slice 1 (Timing) handles StepState + step list. Slice 2 (Approval) handles ApprovalContext + PolicyDecision + approval card. Slice 3 (Interaction) handles tooltips + animations + action_preview.

**Tech Stack:** Python/Pydantic (backend contracts), React/TypeScript (frontend components), CSS animations (globals.css), Zustand (surface store)

**Spec:** `docs/superpowers/specs/2026-04-13-surface-design-phase4-design.md`

---

## Slice 1: Timing (Features 1 + 2)

### Task 1: Widen StepState Backend Contract

**Files:**
- Modify: `backend/src/orchestrator/contracts.py:283-292`
- Test: `backend/tests/test_contracts.py`

- [ ] **Step 1: Write test for widened StepState**

Add to `backend/tests/test_contracts.py`:

```python
from src.orchestrator.contracts import StepState


class TestStepState:
    def test_new_fields_default_none(self):
        s = StepState(step_id="step_001", description="Search KB", status="pending")
        assert s.started_at is None
        assert s.completed_at is None
        assert s.timeout_seconds is None
        assert s.error is None
        assert s.retry_count is None

    def test_new_fields_populated(self):
        s = StepState(
            step_id="step_001",
            description="Search KB",
            status="executing",
            started_at="2026-04-13T10:00:00Z",
            timeout_seconds=60,
        )
        assert s.started_at == "2026-04-13T10:00:00Z"
        assert s.timeout_seconds == 60

    def test_extra_fields_ignored(self):
        s = StepState(
            step_id="step_001",
            description="x",
            status="completed",
            unknown_field="ignored",
        )
        assert not hasattr(s, "unknown_field")

    def test_completed_with_all_fields(self):
        s = StepState(
            step_id="step_001",
            description="Send email",
            status="failed",
            duration_ms=47000,
            started_at="2026-04-13T10:00:00Z",
            completed_at="2026-04-13T10:00:47Z",
            timeout_seconds=60,
            error={"message": "SMTP timeout", "code": "ETIMEDOUT"},
            retry_count=3,
        )
        assert s.error["message"] == "SMTP timeout"
        assert s.retry_count == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_contracts.py::TestStepState -v`
Expected: FAIL — `StepState` does not accept `started_at`, `completed_at`, etc.

- [ ] **Step 3: Widen StepState model**

In `backend/src/orchestrator/contracts.py`, replace the StepState class (lines 283-292):

```python
class StepState(BaseModel):
    """Live status of a single execution step."""

    model_config = ConfigDict(extra="ignore")

    step_id: str
    description: str
    status: Literal["pending", "executing", "completed", "failed", "approval_needed", "user_action"]
    output_summary: str | None = None
    duration_ms: int | None = None
    started_at: str | None = None

    # Evidence (available on demand)
    completed_at: str | None = None
    timeout_seconds: int | None = None
    error: dict | None = None
    retry_count: int | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_contracts.py::TestStepState -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/orchestrator/contracts.py backend/tests/test_contracts.py
git commit -m "feat: widen StepState contract with timing and evidence fields"
```

---

### Task 2: Forward StepState Fields in Graph Executor

**Files:**
- Modify: `backend/src/services/graph_executor.py` (5 StepState construction sites: lines ~356, ~588, ~634, ~665, ~1104)

There are 5 places where `StepState(...)` is constructed. Each needs to forward the new fields from the `TaskStep` model (`s`).

- [ ] **Step 1: Create a helper to build StepState from TaskStep**

At the top of `graph_executor.py` (after imports, before the class), add a module-level helper:

```python
def _step_to_state(s: TaskStep, status_override: str | None = None) -> StepState:
    """Build a StepState from a TaskStep model, forwarding all available fields."""
    from src.orchestrator.contracts import StepState

    status = status_override or s.status
    started_iso = s.started_at.isoformat() if s.started_at else None
    completed_iso = s.completed_at.isoformat() if s.completed_at else None
    duration = (
        int((s.completed_at - s.started_at).total_seconds() * 1000)
        if s.completed_at and s.started_at
        else None
    )
    return StepState(
        step_id=s.step_id,
        description=s.name or (s.input_data or {}).get("capability", s.task_id),
        status=status,
        output_summary=(
            str(s.output_data.get("result", ""))[:200] if s.output_data else None
        ),
        duration_ms=duration,
        started_at=started_iso,
        completed_at=completed_iso,
        timeout_seconds=s.timeout_seconds,
        error=s.error,
        retry_count=s.retry_count if s.retry_count > 0 else None,
    )
```

- [ ] **Step 2: Replace all 5 StepState construction sites**

Replace each inline `StepState(...)` list comprehension with `_step_to_state()`:

**Site 1 — plan_ready (line ~356):**
Replace the list comprehension with:
```python
plan_ready_steps = [
    _step_to_state(s, status_override="pending")
    for s in all_steps
]
```

**Site 2 — completed (line ~588):**
Replace `_final_states` construction with:
```python
_final_states = [_step_to_state(s) for s in _comp_steps]
```

**Site 3 — failed run (line ~634):**
Replace `_fail_states` construction with:
```python
_fail_states = [_step_to_state(s) for s in _fail_steps]
```

**Site 4 — executing (line ~665):**
Replace `_step_states` construction with:
```python
_step_states = [
    _step_to_state(
        s,
        status_override="executing" if s.step_id in (run.current_step_ids or []) else None,
    )
    for s in _all_for_surface
]
```

**Site 5 — step failed (line ~1104):**
Replace `step_states` construction with:
```python
step_states = [
    _step_to_state(
        s,
        status_override="failed" if s.step_id == step.step_id else None,
    )
    for s in all_steps
]
```

- [ ] **Step 3: Run existing graph executor tests**

Run: `cd backend && python -m pytest tests/test_graph_executor.py -v`
Expected: PASS — existing tests should still pass with the refactored helper

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/graph_executor.py
git commit -m "refactor: extract _step_to_state helper, forward all StepState fields"
```

---

### Task 3: Widen Frontend StepState Type

**Files:**
- Modify: `frontend/src/lib/a2ui-types.ts:128-134`

- [ ] **Step 1: Update StepState interface**

In `frontend/src/lib/a2ui-types.ts`, replace the StepState interface (lines 128-134):

```typescript
export interface StepState {
  step_id: string;
  description: string;
  status: "pending" | "executing" | "completed" | "failed" | "approval_needed" | "user_action";
  output_summary: string | null;
  duration_ms: number | null;
  started_at: string | null;

  // Evidence
  completed_at: string | null;
  timeout_seconds: number | null;
  error: Record<string, unknown> | null;
  retry_count: number | null;
}
```

- [ ] **Step 2: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: No new type errors (all new fields are nullable, existing usage unaffected)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/a2ui-types.ts
git commit -m "feat: widen StepState frontend type with timing and evidence fields"
```

---

### Task 4: Add riskLevelColor to Design Tokens

**Files:**
- Modify: `frontend/src/lib/design-tokens.ts`

- [ ] **Step 1: Add riskLevelColor function**

In `frontend/src/lib/design-tokens.ts`, add after the `trustLevelColor` function (after line 142):

```typescript
/** Maps risk level to a Tailwind bg class */
export function riskLevelColor(level: string): string {
  switch (level) {
    case "none":
      return "bg-t-muted";
    case "low":
      return "bg-j-info";
    case "medium":
      return "bg-j-warning";
    case "high":
      return "bg-j-error";
    case "critical":
      return "bg-j-error";
    default:
      return "bg-t-muted";
  }
}

/** Maps risk level to a Tailwind text class */
export function riskLevelTextColor(level: string): string {
  switch (level) {
    case "none":
      return "text-t-muted";
    case "low":
      return "text-j-info";
    case "medium":
      return "text-j-warning";
    case "high":
      return "text-j-error";
    case "critical":
      return "text-j-error";
    default:
      return "text-t-muted";
  }
}
```

- [ ] **Step 2: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/design-tokens.ts
git commit -m "feat: add riskLevelColor and riskLevelTextColor design token helpers"
```

---

### Task 5: Elapsed Timer Hook + Pill Badge

**Files:**
- Modify: `frontend/src/components/a2ui/components/step-list.tsx`

- [ ] **Step 1: Add useElapsedTimer hook**

At the top of `step-list.tsx` (after imports, before the component), add:

```typescript
import { useState, useEffect, useCallback } from "react";

function useElapsedTimer(startedAt: string | null, active: boolean): number {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!active || !startedAt) {
      setElapsedMs(0);
      return;
    }
    const start = new Date(startedAt).getTime();
    const tick = () => setElapsedMs(Date.now() - start);
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt, active]);

  return elapsedMs;
}
```

- [ ] **Step 2: Add ElapsedBadge component**

Below the hook, add a sub-component:

```typescript
function ElapsedBadge({ step }: { step: StepState }) {
  const isExecuting = step.status === "executing";
  const isFailed = step.status === "failed";
  const elapsedMs = useElapsedTimer(step.started_at ?? null, isExecuting);

  if (isExecuting && step.started_at) {
    return (
      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-j-primary/12 text-j-primary text-[11px] shrink-0"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        <span className="w-[5px] h-[5px] rounded-full bg-j-primary animate-pulse-live" />
        {formatDuration(elapsedMs)}
      </span>
    );
  }

  if (isFailed && step.duration_ms != null) {
    return (
      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-j-error/12 text-j-error text-[11px] shrink-0"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {formatDuration(step.duration_ms)}
      </span>
    );
  }

  return null;
}
```

- [ ] **Step 3: Wire ElapsedBadge into step rendering**

In the StepList component, replace the existing duration display (the `{step.duration_ms != null && step.status === "completed" && ...}` block at lines 88-92) with:

```tsx
{step.status === "completed" && step.duration_ms != null && (
  <span className="text-[10px] text-t-tertiary shrink-0">
    {formatDuration(step.duration_ms)}
  </span>
)}
{(step.status === "executing" || step.status === "failed") && (
  <ElapsedBadge step={step} />
)}
```

- [ ] **Step 4: Verify no type errors and visual check**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/a2ui/components/step-list.tsx
git commit -m "feat: add elapsed timer hook and pill badge for executing/failed steps"
```

---

### Task 6: Step Grouping for Long Lists

**Files:**
- Modify: `frontend/src/components/a2ui/components/step-list.tsx`

- [ ] **Step 1: Add showCompletedSteps state and grouping logic**

Add state to the StepList component (after the existing `expandedSteps` state):

```typescript
const [showCompletedSteps, setShowCompletedSteps] = useState(false);
```

- [ ] **Step 2: Add CompletedGroupSummary sub-component**

Below ElapsedBadge, add:

```typescript
function CompletedGroupSummary({
  count,
  totalDurationMs,
  onExpand,
}: {
  count: number;
  totalDurationMs: number;
  onExpand: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onExpand}
      className="flex items-center gap-2 w-full px-3 py-2 rounded-[var(--radius-md)] bg-j-success-soft border border-j-success/12 cursor-pointer hover:bg-j-success/10 transition-colors"
    >
      <span className={`shrink-0 w-5 text-center ${statusTextColor("completed")}`}>✓</span>
      <span className="flex-1 text-left text-xs">
        <span className="text-t-secondary font-medium">{count} steps completed</span>
        {totalDurationMs > 0 && (
          <span className="ml-1.5 text-t-tertiary">{formatDuration(totalDurationMs)} total</span>
        )}
      </span>
      <span className="text-[10px] text-t-tertiary px-1.5 py-0.5 bg-surface-2 rounded-[var(--radius-sm)]">
        ▸ Expand
      </span>
    </button>
  );
}
```

- [ ] **Step 3: Replace the steps.map with grouping logic**

Replace the entire `{steps.map((step) => { ... })}` block in StepList with:

```tsx
const completedSteps = steps.filter((s) => s.status === "completed");
const shouldGroup = completedSteps.length >= 5 && !showCompletedSteps;
const totalCompletedMs = completedSteps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0);
let groupRendered = false;

return (
  <div className="space-y-1.5">
    {showCompletedSteps && completedSteps.length >= 5 && (
      <button
        type="button"
        onClick={() => setShowCompletedSteps(false)}
        className="flex items-center gap-2 w-full px-3 py-2 rounded-[var(--radius-md)] bg-j-success-soft border border-j-success/12 cursor-pointer hover:bg-j-success/10 transition-colors"
      >
        <span className={`shrink-0 w-5 text-center ${statusTextColor("completed")}`}>✓</span>
        <span className="flex-1 text-left text-xs text-t-secondary font-medium">
          {completedSteps.length} steps completed
        </span>
        <span className="text-[10px] text-t-tertiary px-1.5 py-0.5 bg-surface-2 rounded-[var(--radius-sm)]">
          ▾ Collapse
        </span>
      </button>
    )}
    {steps.map((step) => {
      if (step.status === "completed" && shouldGroup) {
        if (!groupRendered) {
          groupRendered = true;
          return (
            <CompletedGroupSummary
              key="__completed_group"
              count={completedSteps.length}
              totalDurationMs={totalCompletedMs}
              onExpand={() => setShowCompletedSteps(true)}
            />
          );
        }
        return null;
      }

      // ... existing step rendering (the <div key={step.step_id}> block) ...
    })}
  </div>
);
```

Note: Keep the existing per-step `<div key={step.step_id}>` rendering intact for all non-grouped steps. When `showCompletedSteps` is true or there are <5 completed steps, all steps render normally. The only change is wrapping `groupRendered` logic around completed steps.

- [ ] **Step 4: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/a2ui/components/step-list.tsx
git commit -m "feat: add step grouping for long lists (5+ completed steps)"
```

---

## Slice 2: Approval (Features 3 + 5 + 6 + 5A + 5C)

### Task 7: Widen PolicyDecision + ApprovalContext Backend Contracts

**Files:**
- Modify: `backend/src/orchestrator/contracts.py:181-199` (PolicyDecision) and `:295-304` (ApprovalContext)
- Test: `backend/tests/test_contracts_v2.py`

- [ ] **Step 1: Write tests for widened contracts**

Add to `backend/tests/test_contracts_v2.py`:

```python
from src.orchestrator.contracts import ApprovalContext


class TestPolicyDecisionTrustFields:
    def test_trust_fields_default_empty(self):
        pd = PolicyDecision(decision="approval_required", risk_level="medium")
        assert pd.trust_level == ""
        assert pd.effective_trust_level == ""
        assert pd.approved_count == 0
        assert pd.rejected_count == 0

    def test_trust_fields_populated(self):
        pd = PolicyDecision(
            decision="approval_required",
            risk_level="high",
            trust_level="first_use",
            effective_trust_level="first_use",
            approved_count=3,
            rejected_count=1,
        )
        assert pd.trust_level == "first_use"
        assert pd.approved_count == 3


class TestApprovalContext:
    def test_new_fields_default(self):
        ctx = ApprovalContext(
            approval_id="apr_001",
            step_description="Send email",
            risk_reasoning="External write",
            trust_context="First use",
        )
        assert ctx.risk_level == ""
        assert ctx.trust_level == ""
        assert ctx.expires_at is None
        assert ctx.triggering_step_id is None
        assert ctx.reversible is True
        assert ctx.blast_radius == "self"
        assert ctx.effective_trust_level == ""
        assert ctx.approved_count == 0
        assert ctx.rejected_count == 0

    def test_new_fields_populated(self):
        ctx = ApprovalContext(
            approval_id="apr_001",
            step_description="Send email",
            risk_reasoning="External write",
            trust_context="First use",
            risk_level="medium",
            trust_level="first_use",
            expires_at="2026-04-13T10:30:00Z",
            triggering_step_id="step_003",
            reversible=False,
            blast_radius="external_multiple",
            effective_trust_level="first_use",
            approved_count=0,
            rejected_count=0,
        )
        assert ctx.risk_level == "medium"
        assert ctx.triggering_step_id == "step_003"
        assert ctx.reversible is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_contracts_v2.py::TestPolicyDecisionTrustFields -v && python -m pytest tests/test_contracts_v2.py::TestApprovalContext -v`
Expected: FAIL

- [ ] **Step 3: Widen PolicyDecision**

In `backend/src/orchestrator/contracts.py`, add to the PolicyDecision class (after `execution_id` field, line ~198):

```python
    trust_level: str = ""
    effective_trust_level: str = ""
    approved_count: int = 0
    rejected_count: int = 0
```

- [ ] **Step 4: Widen ApprovalContext**

Replace the ApprovalContext class (lines 295-304):

```python
class ApprovalContext(BaseModel):
    """Context for an approval gate within a surface update."""

    model_config = ConfigDict(extra="ignore")

    # Primary
    approval_id: str
    step_description: str
    risk_level: str = ""
    trust_level: str = ""
    expires_at: str | None = None
    triggering_step_id: str | None = None
    graduation_hint: str = ""

    # Evidence
    risk_reasoning: str
    trust_context: str
    reversible: bool = True
    blast_radius: str = "self"
    effective_trust_level: str = ""
    approved_count: int = 0
    rejected_count: int = 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_contracts_v2.py -v`
Expected: PASS (all tests including existing ones)

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/contracts.py backend/tests/test_contracts_v2.py
git commit -m "feat: widen PolicyDecision and ApprovalContext with trust and risk fields"
```

---

### Task 8: Enrich TrustEngine.evaluate() Return

**Files:**
- Modify: `backend/src/services/trust_engine.py:89-108`
- Test: `backend/tests/test_trust_engine_v2.py`

- [ ] **Step 1: Write test for enriched PolicyDecision**

Add to `backend/tests/test_trust_engine_v2.py`:

```python
class TestEvaluateReturnsTrustFields:
    """evaluate() must populate trust_level, effective_trust_level, counters."""

    async def test_first_use_returns_trust_fields(self, engine):
        state = _make_trust_state("first_use")
        state.approved_count = 2
        state.rejected_count = 1
        engine._get_trust_state = AsyncMock(return_value=state)
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("autonomous"))

        result = await engine.evaluate("email.send", _make_risk("low"))

        assert result.trust_level == "first_use"
        assert result.effective_trust_level == "first_use"
        assert result.approved_count == 2
        assert result.rejected_count == 1

    async def test_ceiling_limits_effective_level(self, engine):
        state = _make_trust_state("trusted")
        state.approved_count = 15
        state.rejected_count = 0
        engine._get_trust_state = AsyncMock(return_value=state)
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("learning"))

        result = await engine.evaluate("email.send", _make_risk("low"))

        assert result.trust_level == "trusted"
        assert result.effective_trust_level == "learning"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_trust_engine_v2.py::TestEvaluateReturnsTrustFields -v`
Expected: FAIL — `result.trust_level` is `""`

- [ ] **Step 3: Enrich the evaluate() method**

In `backend/src/services/trust_engine.py`, replace lines 104-108:

```python
        return PolicyDecision(
            decision=decision,
            justification=risk_assessment.reasoning,
            risk_level=risk,
            trust_level=state.trust_level,
            effective_trust_level=effective_level,
            approved_count=state.approved_count,
            rejected_count=state.rejected_count,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_trust_engine_v2.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/trust_engine.py backend/tests/test_trust_engine_v2.py
git commit -m "feat: enrich TrustEngine.evaluate() with trust_level, counters on PolicyDecision"
```

---

### Task 9: Forward ApprovalContext Fields in Graph Executor

**Files:**
- Modify: `backend/src/services/graph_executor.py` (2 ApprovalContext construction sites: lines ~858, ~1010)

- [ ] **Step 1: Enrich the TrustEngine approval site (line ~1010)**

Replace the `ApprovalContext(...)` construction at line ~1010:

```python
            await self._emit_surface_update(
                surface_id=surface_id,
                user_id=run.user_id,
                phase="approval_needed",
                approval=ApprovalContext(
                    approval_id=approval.approval_id,
                    step_description=step.name or capability,
                    risk_level=risk.risk_level,
                    trust_level=decision.trust_level,
                    expires_at=approval.expires_at.isoformat() if approval.expires_at else None,
                    triggering_step_id=step.step_id,
                    graduation_hint=decision.justification or "",
                    risk_reasoning=risk.reasoning,
                    trust_context=decision.justification or "",
                    reversible=risk.reversible,
                    blast_radius=risk.blast_radius,
                    effective_trust_level=decision.effective_trust_level,
                    approved_count=decision.approved_count,
                    rejected_count=decision.rejected_count,
                ),
                workspace_id=run.workspace_id,
            )
```

- [ ] **Step 2: Enrich the legacy approval site (line ~858)**

Replace the `ApprovalContext(...)` construction at line ~858:

```python
                        await self._emit_surface_update(
                            surface_id=surface_id,
                            user_id=run.user_id,
                            phase="approval_needed",
                            approval=ApprovalContext(
                                approval_id=approval.approval_id,
                                step_description=step.name or capability,
                                risk_level=risk_level,
                                trust_level="",
                                expires_at=approval.expires_at.isoformat() if approval.expires_at else None,
                                triggering_step_id=step.step_id,
                                risk_reasoning=f"Risk: {risk_level}",
                                trust_context="Legacy approval gate",
                            ),
                            workspace_id=run.workspace_id,
                        )
```

- [ ] **Step 3: Run existing tests**

Run: `cd backend && python -m pytest tests/test_graph_executor.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/graph_executor.py
git commit -m "feat: forward enriched ApprovalContext fields in graph executor emissions"
```

---

### Task 10: Widen Frontend ApprovalContext + SuggestedActionRef Types

**Files:**
- Modify: `frontend/src/lib/a2ui-types.ts:91-95` (SuggestedActionRef) and `:136-142` (ApprovalContext)

- [ ] **Step 1: Widen ApprovalContext interface**

Replace lines 136-142:

```typescript
export interface ApprovalContext {
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

- [ ] **Step 2: Widen SuggestedActionRef interface**

Replace lines 91-95:

```typescript
export interface SuggestedActionRef {
  description: string;
  capability: string;
  action_input: Record<string, unknown>;
  action_preview: string;
}
```

- [ ] **Step 3: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: Type errors in `inline-approval.tsx` (now expects new fields) — this is expected and will be fixed in the next task.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/a2ui-types.ts
git commit -m "feat: widen ApprovalContext and SuggestedActionRef frontend types"
```

---

### Task 11: Enriched Inline Approval Card

**Files:**
- Modify: `frontend/src/components/a2ui/components/inline-approval.tsx`

This is the largest single task — it adds: timeout countdown, risk/trust badges, evidence expand, reject confirmation with reason, expiration handling, and visual connector.

- [ ] **Step 1: Add countdown hook and state**

Replace the full `inline-approval.tsx` file with:

```typescript
"use client";

import { useState, useCallback, useEffect } from "react";
import type { ApprovalContext } from "@/lib/a2ui-types";
import { useWsActionStore } from "@/stores/ws-action-store";
import { riskLevelColor, riskLevelTextColor, trustLevelColor } from "@/lib/design-tokens";
import { Modal } from "@/components/ui/modal";

function useCountdown(expiresAt: string | null): number {
  const [remainingMs, setRemainingMs] = useState(() => {
    if (!expiresAt) return Infinity;
    return new Date(expiresAt).getTime() - Date.now();
  });

  useEffect(() => {
    if (!expiresAt) return;
    const tick = () => setRemainingMs(new Date(expiresAt).getTime() - Date.now());
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  return remainingMs;
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "Expired";
  const totalSec = Math.ceil(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min > 0) return `${min}m ${sec.toString().padStart(2, "0")}s`;
  return `${sec}s`;
}

interface InlineApprovalCardProps {
  approval: ApprovalContext;
}

export function InlineApprovalCard({ approval }: InlineApprovalCardProps) {
  const sendAction = useWsActionStore((s) => s.sendAction);
  const [showRejectConfirm, setShowRejectConfirm] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const remainingMs = useCountdown(approval.expires_at ?? null);
  const isExpired = approval.expires_at != null && remainingMs <= 0;
  const isUrgent = approval.expires_at != null && remainingMs > 0 && remainingMs <= 120_000;

  const handleApprove = useCallback(() => {
    if (isExpired) return;
    sendAction("approve", { id: approval.approval_id });
  }, [sendAction, approval.approval_id, isExpired]);

  const handleRejectClick = useCallback(() => {
    if (isExpired) return;
    setShowRejectConfirm(true);
  }, [isExpired]);

  const handleRejectConfirm = useCallback(() => {
    sendAction("reject", { id: approval.approval_id, reason: rejectReason || undefined });
    setShowRejectConfirm(false);
    setRejectReason("");
  }, [sendAction, approval.approval_id, rejectReason]);

  const handleEdit = useCallback(() => {
    if (isExpired) return;
    sendAction("edit_before_approve", { id: approval.approval_id });
  }, [sendAction, approval.approval_id, isExpired]);

  const riskBorder = approval.risk_level === "high" ? "border-j-error/30" : "border-j-warning/30";
  const riskBg = approval.risk_level === "high" ? "bg-j-error-soft" : "bg-j-warning-soft";

  return (
    <>
      <div className={`rounded-[var(--radius-lg)] border ${riskBorder} ${riskBg} p-4 space-y-3`}>
        {/* Header with countdown */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-j-warning">&#9888;</span>
            <span className="text-sm font-medium text-t-primary">Approval Required</span>
          </div>
          {approval.expires_at && (
            <span className={`text-[11px] flex items-center gap-1 ${isExpired ? "text-j-error font-medium" : isUrgent ? "text-j-error animate-pulse" : "text-j-warning"}`}>
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" className="opacity-70">
                <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.2" />
                <path d="M8 4.5V8.5L10.5 10" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
              </svg>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>{formatCountdown(remainingMs)}</span>
            </span>
          )}
        </div>

        {/* Step description */}
        <p className="text-sm font-semibold text-t-primary">{approval.step_description}</p>

        {/* Primary badges: risk + trust + blast radius */}
        <div className="flex flex-wrap gap-1.5">
          {approval.risk_level && (
            <span className={`text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] ${riskLevelColor(approval.risk_level)} ${riskLevelTextColor(approval.risk_level)} font-semibold uppercase tracking-wider`}>
              {approval.risk_level} risk
            </span>
          )}
          {approval.trust_level && (
            <span className={`text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] ${trustLevelColor(approval.trust_level)} text-white font-medium`}>
              {approval.trust_level.replace("_", " ")}
            </span>
          )}
          {!approval.reversible && (
            <span className="text-[10px] px-2 py-0.5 rounded-[var(--radius-sm)] bg-surface-2 text-t-tertiary">
              Irreversible
            </span>
          )}
        </div>

        {/* Evidence section (collapsible) */}
        <details className="group">
          <summary className="text-[11px] text-t-muted cursor-pointer select-none py-1 hover:text-t-secondary transition-colors">
            Why does this need approval?
          </summary>
          <div className="rounded-[var(--radius-md)] bg-surface-1 border-l-[3px] border-l-j-warning p-3 space-y-1.5 mt-1.5 animate-fade-in">
            <p className="text-xs text-t-tertiary">{approval.risk_reasoning}</p>
            <div className="flex flex-wrap gap-2 text-[10px] text-t-muted">
              {approval.blast_radius !== "self" && <span>Blast radius: {approval.blast_radius.replace("_", " ")}</span>}
              {approval.approved_count > 0 && <span>Approved: {approval.approved_count}</span>}
              {approval.rejected_count > 0 && <span>Rejected: {approval.rejected_count}</span>}
            </div>
          </div>
        </details>

        {/* Graduation hint */}
        {approval.graduation_hint && (
          <div className="bg-j-info-soft rounded-[var(--radius-md)] px-3 py-2 flex items-start gap-2">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" className="text-j-info shrink-0 mt-0.5">
              <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.3" />
              <path d="M8 7v4M8 5.5v0" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
            <p className="text-xs text-j-info">{approval.graduation_hint}</p>
          </div>
        )}

        {/* Action buttons */}
        <div className="flex items-center gap-2.5 pt-1">
          <button type="button" onClick={handleApprove} disabled={isExpired}
            className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-success text-white hover:bg-j-success/90 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed">
            Approve
          </button>
          <button type="button" onClick={handleEdit} disabled={isExpired}
            className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed">
            Edit
          </button>
          <button type="button" onClick={handleRejectClick} disabled={isExpired}
            className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed">
            Reject
          </button>
        </div>
      </div>

      {/* Reject confirmation modal */}
      <Modal open={showRejectConfirm} onClose={() => setShowRejectConfirm(false)} title="Reject this action?" size="sm">
        <div className="space-y-3">
          <p className="text-sm text-t-secondary">
            This will cancel &ldquo;{approval.step_description}&rdquo;. The task will be marked as rejected.
          </p>
          <div>
            <label className="text-xs text-t-muted block mb-1">Optionally explain why:</label>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="e.g., wrong recipients, needs review first"
              rows={2}
              className="w-full text-xs bg-surface-1 border border-b-secondary rounded-[var(--radius-md)] px-3 py-2 text-t-primary placeholder:text-t-muted resize-none focus:outline-none focus:ring-1 focus:ring-j-primary/50"
            />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setShowRejectConfirm(false)}
              className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer">
              Cancel
            </button>
            <button type="button" onClick={handleRejectConfirm}
              className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer">
              Yes, Reject
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}
```

- [ ] **Step 2: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/inline-approval.tsx
git commit -m "feat: enriched approval card with countdown, badges, evidence, reject confirmation"
```

---

### Task 12: Visual Connection — Triggering Step Highlight

**Files:**
- Modify: `frontend/src/components/a2ui/components/step-list.tsx` (add `triggeringStepId` prop)
- Modify: `frontend/src/components/a2ui/components/execution-surface.tsx` (pass prop + connector)

- [ ] **Step 1: Add triggeringStepId prop to StepList**

Update the StepListProps interface:

```typescript
interface StepListProps {
  steps: StepState[];
  currentStep: string | null;
  triggeringStepId?: string | null;
}
```

Update the component signature:

```typescript
export function StepList({ steps, currentStep, triggeringStepId }: StepListProps) {
```

- [ ] **Step 2: Add triggering step highlight in step rendering**

In the step rendering, add a check for `triggeringStepId`. The triggering step gets warning-colored highlighting instead of the primary highlight. Update the className logic for the step `<div>`:

```tsx
const isTriggering = step.step_id === triggeringStepId;

// In the className:
className={`flex items-start gap-2 text-sm ${
  isTriggering
    ? "bg-j-warning-soft border-l-2 border-l-j-warning py-2 px-3 rounded-[var(--radius-sm)]"
    : isCurrent
      ? "bg-j-primary-soft border-l-2 border-l-j-primary py-2 px-3 rounded-[var(--radius-sm)]"
      : "py-1.5 px-2"
}`}
```

Add "awaiting" badge for the triggering step (after the description span):

```tsx
{isTriggering && (
  <span className="text-[9px] px-1.5 py-0.5 rounded bg-j-warning/12 text-j-warning uppercase font-medium shrink-0">
    awaiting
  </span>
)}
```

- [ ] **Step 3: Wire triggeringStepId in execution-surface.tsx**

In `execution-surface.tsx`, update both StepList usages to pass the prop:

```tsx
<StepList
  steps={steps}
  currentStep={currentStep}
  triggeringStepId={approval?.triggering_step_id ?? null}
/>
```

Add the connector line between step list and approval card (in the `approval_needed` phase section):

```tsx
{phase === "approval_needed" && approval && (
  <>
    {approval.triggering_step_id && (
      <div className="ml-5 w-px h-2 bg-j-warning/30" />
    )}
    <div className="animate-slide-in-up">
      <InlineApprovalCard approval={approval} />
    </div>
  </>
)}
```

- [ ] **Step 4: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/a2ui/components/step-list.tsx frontend/src/components/a2ui/components/execution-surface.tsx
git commit -m "feat: visual connection between triggering step and approval card"
```

---

## Slice 3: Interaction (Features 4 + 7 + 5B)

### Task 13: Add action_preview to SuggestedActionRef Backend

**Files:**
- Modify: `backend/src/orchestrator/contracts.py:255-262` (SuggestedActionRef)
- Modify: `backend/src/orchestrator/jarvis.py:2221-2227` (insight surface builder)

- [ ] **Step 1: Add action_preview field**

In `backend/src/orchestrator/contracts.py`, add to the SuggestedActionRef class:

```python
class SuggestedActionRef(BaseModel):
    """Reference to a suggested action stored in the surface payload."""

    model_config = ConfigDict(extra="ignore")

    description: str
    capability: str
    action_input: dict[str, Any] = Field(default_factory=dict)
    action_preview: str = ""
```

- [ ] **Step 2: Populate action_preview in _push_insight_surface**

In `backend/src/orchestrator/jarvis.py`, update the SuggestedActionRef construction at line ~2222:

```python
            suggested_actions = [
                SuggestedActionRef(
                    description=a.description,
                    capability=a.capability,
                    action_input=a.action_input,
                    action_preview=_build_action_preview(a.capability, a.description),
                )
                for a in assessment.suggested_actions
            ]
```

Add the helper function nearby (before `_push_insight_surface`):

```python
def _build_action_preview(capability: str, description: str) -> str:
    """Generate tooltip preview text for an insight action based on capability type."""
    cap = capability.lower()
    if any(w in cap for w in ("send", "create", "update", "delete", "write")):
        return f"Creates a task to {description.lower()}"
    if any(w in cap for w in ("read", "search", "fetch", "list", "get")):
        return f"Fetches {capability.split('.')[-1]} data without taking action"
    if any(w in cap for w in ("respond", "reason", "summarize")):
        return f"Generates a response about {description.lower()}"
    return ""
```

- [ ] **Step 3: Run existing tests**

Run: `cd backend && python -m pytest tests/ -v -k "insight or contract" --timeout=30`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/orchestrator/contracts.py backend/src/orchestrator/jarvis.py
git commit -m "feat: add action_preview to SuggestedActionRef with capability-based generation"
```

---

### Task 14: CSS-Only Tooltip Component

**Files:**
- Create: `frontend/src/components/ui/tooltip.tsx`

- [ ] **Step 1: Create the tooltip component**

```typescript
"use client";

import type { ReactNode } from "react";

interface TooltipProps {
  text: string;
  children: ReactNode;
  position?: "top" | "bottom";
}

export function Tooltip({ text, children, position = "top" }: TooltipProps) {
  if (!text) return <>{children}</>;

  const posClass =
    position === "top"
      ? "bottom-full left-1/2 -translate-x-1/2 mb-2"
      : "top-full left-1/2 -translate-x-1/2 mt-2";

  const arrowClass =
    position === "top"
      ? "top-full left-1/2 -translate-x-1/2 border-t-[#27272a] border-t-[5px] border-x-transparent border-x-[5px] border-b-0"
      : "bottom-full left-1/2 -translate-x-1/2 border-b-[#27272a] border-b-[5px] border-x-transparent border-x-[5px] border-t-0";

  return (
    <span className="relative group inline-flex">
      {children}
      <span
        role="tooltip"
        className={`absolute ${posClass} z-50 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-150 delay-300 whitespace-normal max-w-[280px] px-3 py-2 text-[11px] text-[#d4d4d8] bg-[#27272a] border border-white/10 rounded-[var(--radius-md)] shadow-lg`}
      >
        {text}
        <span className={`absolute ${arrowClass} w-0 h-0`} />
      </span>
    </span>
  );
}
```

- [ ] **Step 2: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/tooltip.tsx
git commit -m "feat: add CSS-only Tooltip component"
```

---

### Task 15: Wire Tooltips into Insight Surface + Dismiss Confirmation

**Files:**
- Modify: `frontend/src/components/a2ui/components/insight-surface.tsx`

- [ ] **Step 1: Update insight-surface.tsx**

Replace the full file:

```typescript
"use client";

import { useState, useCallback } from "react";
import type { InsightData } from "@/lib/a2ui-types";
import { dismissInsight } from "@/lib/api";
import { useSurfaceStore } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { Tooltip } from "@/components/ui/tooltip";
import { Modal } from "@/components/ui/modal";

const sourceIcons: Record<string, string> = {
  gmail: "\u2709\uFE0F",
  github: "\uD83D\uDC19",
  calendar: "\uD83D\uDCC5",
  slack: "\uD83D\uDCAC",
  linear: "\uD83D\uDCCB",
};

interface InsightSurfaceProps {
  surfaceId: string;
  insightData: InsightData;
}

export function InsightSurface({ surfaceId, insightData }: InsightSurfaceProps) {
  const [dismissing, setDismissing] = useState(false);
  const [acting, setActing] = useState<number | null>(null);
  const [showDismissConfirm, setShowDismissConfirm] = useState(false);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const sendAction = useWsActionStore((s) => s.sendAction);

  const handleDismissConfirm = useCallback(async () => {
    setShowDismissConfirm(false);
    setDismissing(true);
    try {
      await dismissInsight(surfaceId);
      removeSurface(surfaceId);
    } catch {
      setDismissing(false);
    }
  }, [surfaceId, removeSurface]);

  const handleAction = useCallback(
    (index: number) => {
      if (!sendAction) return;
      setActing(index);
      sendAction("execute_insight", {
        surface_id: surfaceId,
        action_index: index,
      });
    },
    [sendAction, surfaceId],
  );

  const icon = sourceIcons[insightData.signal_source] ?? "\uD83D\uDD14";

  return (
    <>
      <div className="space-y-3">
        {/* 1. Signal summary */}
        <p className="text-sm text-t-primary font-semibold">
          {insightData.signal_summary}
        </p>

        {/* 2. Source + relevance */}
        <div className="flex items-center gap-1.5 text-xs text-t-muted">
          <span>{icon}</span>
          <span>{insightData.signal_source}</span>
          {insightData.relevance_score >= 0.7 && (
            <>
              <span>&middot;</span>
              <span className="text-j-warning font-medium">High relevance</span>
            </>
          )}
        </div>

        {/* 3. Relevance reasoning */}
        {insightData.relevance_reasoning && (
          <p className="text-xs text-t-tertiary">
            {insightData.relevance_reasoning}
          </p>
        )}

        {/* 4. Related goals */}
        {insightData.related_goals.length > 0 && (
          <div>
            <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-1.5">Related goals</p>
            <div className="flex flex-wrap gap-1">
              {insightData.related_goals.map((goal, i) => (
                <span
                  key={i}
                  className="text-[10px] px-1.5 py-0.5 rounded-full bg-j-info-soft text-j-info"
                >
                  {goal}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* 5. Suggested actions + dismiss */}
        {(insightData.suggested_actions.length > 0 || insightData.dismiss_available) && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {insightData.suggested_actions.map((action, i) => (
              <Tooltip
                key={i}
                text={action.action_preview || `Execute: ${action.description}`}
              >
                <button
                  type="button"
                  onClick={() => handleAction(i)}
                  disabled={acting !== null}
                  className={`text-xs px-3 py-1.5 rounded-[var(--radius-md)] transition-colors disabled:opacity-50 cursor-pointer ${
                    i === 0
                      ? "bg-j-primary text-j-primary-fg font-medium hover:bg-j-primary-hover"
                      : "bg-surface-2 text-t-secondary hover:bg-surface-3"
                  }`}
                >
                  {acting === i ? "Starting..." : action.description}
                </button>
              </Tooltip>
            ))}
            {insightData.dismiss_available && (
              <button
                type="button"
                onClick={() => setShowDismissConfirm(true)}
                disabled={dismissing}
                className="text-xs text-t-muted hover:text-t-secondary transition-colors disabled:opacity-50 cursor-pointer ml-auto"
              >
                {dismissing ? "Dismissing..." : "Dismiss"}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Dismiss confirmation modal */}
      <Modal open={showDismissConfirm} onClose={() => setShowDismissConfirm(false)} title="Dismiss this insight?" size="sm">
        <div className="space-y-3">
          <p className="text-sm text-t-secondary">
            This insight will be removed from your workspace.
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setShowDismissConfirm(false)}
              className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer">
              Cancel
            </button>
            <button type="button" onClick={handleDismissConfirm}
              className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer">
              Yes, Dismiss
            </button>
          </div>
        </div>
      </Modal>
    </>
  );
}
```

- [ ] **Step 2: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/insight-surface.tsx
git commit -m "feat: add action tooltips and dismiss confirmation to insight surface"
```

---

### Task 16: Wire Tooltips into Approval Card

**Files:**
- Modify: `frontend/src/components/a2ui/components/inline-approval.tsx`

- [ ] **Step 1: Add Tooltip import and wrap buttons**

Add import at top of `inline-approval.tsx`:

```typescript
import { Tooltip } from "@/components/ui/tooltip";
```

Wrap each action button with `<Tooltip>`:

```tsx
<div className="flex items-center gap-2.5 pt-1">
  <Tooltip text="Jarvis will proceed with this action">
    <button type="button" onClick={handleApprove} disabled={isExpired}
      className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-success text-white hover:bg-j-success/90 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed">
      Approve
    </button>
  </Tooltip>
  <Tooltip text="Review and modify before executing">
    <button type="button" onClick={handleEdit} disabled={isExpired}
      className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed">
      Edit
    </button>
  </Tooltip>
  <Tooltip text="Cancel this action (opens confirmation)">
    <button type="button" onClick={handleRejectClick} disabled={isExpired}
      className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed">
      Reject
    </button>
  </Tooltip>
</div>
```

- [ ] **Step 2: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/inline-approval.tsx
git commit -m "feat: add action preview tooltips to approval card buttons"
```

---

### Task 17: Phase Transition Animations

**Files:**
- Modify: `frontend/src/components/a2ui/components/execution-surface.tsx`

- [ ] **Step 1: Add animation wrappers to phase sections**

In `execution-surface.tsx`, add `transition-colors duration-200` to the phase label span:

```tsx
<span className={`text-xs font-medium transition-colors duration-200 ${phaseClass}`}>{labelText}</span>
```

Wrap the planning spinner in an animation div:

```tsx
{phase === "planning" && (
  <div className="animate-fade-in flex flex-col items-center gap-2 py-6">
    ...existing spinner content...
  </div>
)}
```

Wrap the step list section:

```tsx
{phase !== "planning" && steps.length > 0 && (
  <div key={`steps-${phase}`} className="animate-slide-in-up">
    <StepList steps={steps} currentStep={currentStep} triggeringStepId={approval?.triggering_step_id ?? null} />
  </div>
)}
```

Wrap the results summary:

```tsx
{phase === "completed" && results && (
  <div className="animate-fade-in rounded-[var(--radius-lg)] bg-j-success-soft border border-j-success/20 p-4">
    ...existing results content...
  </div>
)}
```

Wrap the failure context:

```tsx
{phase === "failed" && (
  <div className="animate-fade-in">
    ...existing failure content...
  </div>
)}
```

- [ ] **Step 2: Verify no type errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/execution-surface.tsx
git commit -m "feat: add phase transition animations to execution surface"
```

---

### Task 18: Final Verification

- [ ] **Step 1: Run all backend tests**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`
Expected: PASS

- [ ] **Step 2: Run frontend type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Run frontend lint**

Run: `cd frontend && npm run lint`
Expected: PASS (or only pre-existing warnings)

- [ ] **Step 4: Run backend lint**

Run: `cd backend && ruff check src/ tests/`
Expected: PASS

- [ ] **Step 5: Start dev server and visually verify**

Run: `cd frontend && npm run dev`
Open http://localhost:3000 and verify:
- Execution surfaces render without errors
- If test data available: step grouping, elapsed timer, approval card with badges are visible

- [ ] **Step 6: Final commit if any lint fixes**

```bash
git add -u
git commit -m "chore: lint fixes for Phase 4 interaction features"
```
