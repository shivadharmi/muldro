# Fix-10: Frontend & Validation Cleanup

**Branch**: `improve-overall-system-v2`
**Date**: 2026-04-11
**Scope**: Remaining frontend issues, missing field constraints, minor model fixes

---

## Phase 1: Backend Pydantic Constraint Tightening

### Task 1.1: M-3 — `BriefingFeedbackRequest.feedback_type` Literal constraint
**File**: `backend/src/api/schemas.py:53`
**Current** (line 53):
```python
feedback_type: str  # "rating" | "item_acted_on" | "item_dismissed" | "follow_up_asked"
```
**Fix**: Change to:
```python
feedback_type: Literal["rating", "item_acted_on", "item_dismissed", "follow_up_asked"]
```

### Task 1.2: M-4 — `BriefingFeedbackRequest.rating` Field constraint
**File**: `backend/src/api/schemas.py:54`
**Current** (line 54):
```python
rating: int | None = None  # 1-5 when feedback_type="rating"
```
**Fix**: Change to:
```python
rating: int | None = Field(None, ge=1, le=5)
```
**Note**: Requires adding `Field` to the import on line 6. Currently imports `BaseModel, ConfigDict` — add `Field`.

### Task 1.3: M-5 — Schedule schemas Literal constraints
**File**: `backend/src/api/schemas.py:324-335`

Three fields need Literal constraints in `ScheduleCreateRequest`:
- Line 328: `schedule_type: str = "recurring"` -> `schedule_type: Literal["recurring", "one_shot"] = "recurring"`
- Line 334: `source: str = "user"` -> `source: Literal["system", "user", "reflection"] = "user"`
- Line 335: `priority: str = "medium"` -> `priority: Literal["low", "medium", "high"] = "medium"`

Also update `ScheduleUpdateRequest` (line 347):
- `priority: str | None = None` -> `priority: Literal["low", "medium", "high"] | None = None`

**Note**: `Literal` is already imported on line 4.

### Task 1.4: M-6 — `PerceptionDecision.next_check_seconds` minimum
**File**: `backend/src/orchestrator/contracts.py:174`
**Current** (line 174):
```python
next_check_seconds: int | None = None
```
**Fix**: Change to:
```python
next_check_seconds: int | None = Field(None, ge=30)
```
Minimum of 30 seconds prevents tight polling loops. `Field` is already imported on line 12.

### Task 1.5: M-7 — `TimePolicyRule.start_hour/end_hour` constraints
**File**: `backend/src/api/routes_trust.py:81-82`
**Current** (lines 81-82):
```python
start_hour: int
end_hour: int
```
**Fix**: Change to:
```python
start_hour: int = Field(ge=0, le=23)
end_hour: int = Field(ge=0, le=23)
```
**Note**: Requires adding `Field` to the pydantic import on line 6. Currently imports `BaseModel, ConfigDict`.

The runtime validation on line 191 (`if not (0 <= p.start_hour <= 23 ...`) becomes redundant but can stay as defense-in-depth.

### Task 1.6: M-8 — `RelevanceAssessment(**data)` -> `model_validate`
**File**: `backend/src/services/relevance_assessor.py:128`
**Current** (line 128):
```python
assessment = RelevanceAssessment(**data)
```
**Fix**: Change to:
```python
assessment = RelevanceAssessment.model_validate(data)
```
This properly invokes Pydantic V2 validation including `extra="ignore"`.

### Task 1.7: M-9 — Trust API models ConfigDict (verify)
**File**: `backend/src/api/routes_trust.py:19-94`
**Status**: All 11 models already have `model_config = ConfigDict(extra="ignore")` (lines 20, 29, 38, 43, 55, 63, 68, 74, 80, 88, 93). **No action needed** — Fix-5 or prior work already addressed this.

### Task 1.8: L-3 — `HealthResponse` ConfigDict (verify)
**File**: `backend/src/api/schemas.py:377`
**Status**: Already has `model_config = ConfigDict(extra="ignore")` on line 377. **No action needed.**

### Task 1.9: L-5 — `DismissResponse.status` Literal
**File**: `backend/src/api/routes_insights.py:25`
**Current** (line 25):
```python
status: str = "dismissed"
```
**Fix**: Change to:
```python
status: Literal["dismissed"] = "dismissed"
```
**Note**: Requires adding `Literal` import. Check existing imports in the file.

### Task 1.10: L-20 — `RuntimeEventResponse.payload` default_factory (verify)
**File**: `backend/src/api/schemas/runtime.py:41`
**Current** (line 41):
```python
payload: dict = Field(default_factory=dict)
```
**Status**: Already uses `Field(default_factory=dict)`. **No action needed.**

---

## Phase 2: Backend Model Validators & Comment Fixes

### Task 2.1: L-4 — `DetailConfig.default_tab` cross-validation
**File**: `backend/src/ui/contracts.py:175-181`
**Current**: `DetailConfig` has `tabs` and `default_tab` but no cross-validation.
**Fix**: Add a `@model_validator(mode="after")` after the field definitions:
```python
from pydantic import model_validator

class DetailConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tabs: list[DetailTab]
    default_tab: str | None = None

    @model_validator(mode="after")
    def _check_default_tab(self) -> "DetailConfig":
        if self.default_tab is not None and self.tabs:
            tab_ids = [t.id for t in self.tabs]
            if self.default_tab not in tab_ids:
                msg = f"default_tab '{self.default_tab}' not in tabs: {tab_ids}"
                raise ValueError(msg)
        return self
```

### Task 2.2: L-9 — Stale comment in capability_resolver
**File**: `backend/src/services/capability_resolver.py:95`
**Current** (line 95):
```python
``"perceiver"`` is a future agent name (Observer + Researcher merge in Spec 1B-i).
```
**Fix**: Update to:
```python
``"perceiver"`` handles information gathering (merged from Observer + Researcher).
```

### Task 2.3: L-12 — `PRESENTER_PROMPT` stale decision type examples
**File**: `backend/src/orchestrator/prompts.py:576-591`
**Current**: Examples reference `draft_reply`, `read_source`, `research` — legacy decision-type language.
**Fix**: Rewrite examples to use capability-based language:
```
Plan goal: draft a follow-up email to investor
-> "I've drafted a follow-up email to John about the investor meeting. The draft is in your Gmail — \
review it and let me know if you'd like changes before sending."

Plan goal: check email for updates
-> "You have 5 unread emails. The most important is from Sarah Chen about the Series A term sheet — \
she's asking for a response by Friday. Two others are newsletters, and two are meeting invites."

Plan goal: research competitor Acme Corp
-> "Here's what I found about Acme Corp: [structured findings]. Key takeaway: they raised $10M \
last quarter and are expanding into your market segment. Want me to dig deeper into their product?"

Something failed:
-> "I wasn't able to check your Gmail — it looks like the connection needs to be re-authorized. \
You can fix this in Settings -> Connectors."
```

---

## Phase 3: Frontend Fixes

### Task 3.1: M-34 — Settings page loading/disable on handlers
**File**: `frontend/src/app/settings/page.tsx:110-170`
**Current**: `handlePolicyChange`, `handleBudgetSave`, `handleCeilingChange`, `handleResetTrust` have no loading state — rapid clicking causes race conditions.
**Fix**: Add a `useState` for each async action group:
```typescript
const [policyLoading, setPolicyLoading] = useState(false);
const [budgetSaving, setBudgetSaving] = useState(false);
const [ceilingLoading, setCeilingLoading] = useState<string | null>(null); // capability being changed
const [resetLoading, setResetLoading] = useState<string | null>(null); // capability being reset
```
Wrap each handler with set-before/clear-after pattern. Disable the corresponding UI elements while loading:
- Policy radio buttons: `disabled={policyLoading}` on each `<input type="radio">`
- Budget save button: `disabled={budgetSaving}`
- Ceiling select: `disabled={ceilingLoading === entry.capability}`
- Reset button: `disabled={resetLoading === entry.capability}`

Place the new `useState` calls after line 81 (after existing hooks, before `useEffect`).

### Task 3.2: M-35 — `reconnectTimer` ref type mismatch
**File**: `frontend/src/hooks/use-jarvis-ws.ts:37`
**Current** (line 37):
```typescript
const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
```
**Status**: Already uses `ReturnType<typeof setTimeout>`. The initial value `undefined` is valid because the generic permits it in React 19 / TS strict mode. **No action needed** — this was already fixed.

### Task 3.3: M-36 — Non-deterministic surface sort for null `created_at`
**File**: `frontend/src/app/page.tsx:75`
**Current** (line 75):
```typescript
return b.created_at.localeCompare(a.created_at);
```
**Fix**: Add secondary sort by surface ID for stability:
```typescript
const dateCompare = b.created_at.localeCompare(a.created_at);
return dateCompare !== 0 ? dateCompare : a.id.localeCompare(b.id);
```

### Task 3.4: M-37 — `StepState.status` frontend vs backend alignment
**File**: `frontend/src/lib/a2ui-types.ts:131`
**Frontend** (line 131):
```typescript
status: "pending" | "executing" | "completed" | "failed" | "approval_needed" | "user_action";
```
**Backend** (`contracts.py:289`):
```python
status: Literal["pending", "executing", "completed", "failed", "approval_needed", "user_action"]
```
**Status**: Frontend and backend Literals already match exactly. **No action needed.**

### Task 3.5: L-18 — `PlanOutput` type in wrong file
**File**: `frontend/src/lib/api.ts:491-507`
**Current**: `CapabilityGap`, `PlanOutput` interfaces defined in `api.ts`.
**Fix**:
1. Move `PlanStep` (if in api.ts), `CapabilityGap`, and `PlanOutput` interfaces to `frontend/src/lib/a2ui-types.ts` (or a new `frontend/src/lib/types/plan.ts`).
2. Re-export from `api.ts` for backward compatibility:
   ```typescript
   export type { PlanOutput, CapabilityGap } from "./a2ui-types";
   ```
3. Check all imports of `PlanOutput` to ensure they still resolve.

---

## Summary

| ID | Severity | Action | File |
|----|----------|--------|------|
| M-3 | MEDIUM | Literal constraint | `schemas.py:53` |
| M-4 | MEDIUM | Field(ge=1,le=5) | `schemas.py:54` |
| M-5 | MEDIUM | Literal constraints x4 | `schemas.py:328,334,335,347` |
| M-6 | MEDIUM | Field(ge=30) | `contracts.py:174` |
| M-7 | MEDIUM | Field(ge=0,le=23) | `routes_trust.py:81-82` |
| M-8 | MEDIUM | model_validate | `relevance_assessor.py:128` |
| M-9 | MEDIUM | Already fixed | `routes_trust.py` |
| M-34 | MEDIUM | Loading states | `settings/page.tsx` |
| M-35 | MEDIUM | Already fixed | `use-jarvis-ws.ts:37` |
| M-36 | MEDIUM | Stable sort | `page.tsx:75` |
| M-37 | MEDIUM | Already aligned | `a2ui-types.ts:131` |
| L-3 | LOW | Already fixed | `schemas.py:377` |
| L-4 | LOW | model_validator | `ui/contracts.py:175` |
| L-5 | LOW | Literal["dismissed"] | `routes_insights.py:25` |
| L-9 | LOW | Update comment | `capability_resolver.py:95` |
| L-12 | LOW | Update examples | `prompts.py:576` |
| L-18 | LOW | Move type to types file | `api.ts:497` |
| L-20 | LOW | Already fixed | `runtime.py:41` |

**Already fixed (no action)**: M-9, M-35, M-37, L-3, L-20 (5 items)
**Needs implementation**: 13 items across 3 phases

**Estimated effort**: ~1-2 hours
**Risk**: Low — constraint additions are additive; existing valid data passes. Frontend changes are UI-only.
