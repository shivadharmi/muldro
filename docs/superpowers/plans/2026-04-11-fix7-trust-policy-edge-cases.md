# Fix-7: Trust & Policy Edge Cases

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix graduation logic bugs, naming mismatches, and trust taxonomy issues across the trust/policy subsystem.

**Architecture:** Surgical fixes -- no redesigns. Each task is independent. Test-first for all changes.

**Tech Stack:** Python 3.12, pytest, SQLAlchemy, ruff (line-length 100)

---

## Phase 1: HIGH Priority Fixes

### Task 1: [HIGH] H-4 -- `graduate_trust()` stuck-at-learning edge case

**Context:** `backend/src/services/risk_assessor.py:167-195`. States with `approved_count >= 25` but `rejection_rate >= 0.05` (and `< 0.10`) fall through to the `approved_count >= 10` branch which returns `"learning"` because `rejection_rate >= 0.10` is not met -- but also cannot reach `"trusted"` because the `rejection_rate < 0.10` check on line 188 fails when rate is exactly 0.10. More critically, states with `approved_count >= 25` and `0.05 <= rejection_rate < 0.10` correctly return `"trusted"` via the elif on line 188, but states with `approved_count >= 10` and `rejection_rate == 0.10` (exactly) fall through to `"first_use"` because `rejection_rate < 0.10` is strict.

The real bug: a state with e.g. 25 approved, 3 rejected (rejection_rate ~0.107) cannot graduate past `"learning"` despite demonstrating strong usage. The volume of approvals warrants at least `"trusted"`.

**Fix:** Add a high-volume override: if `approved_count >= 25` and `rejection_rate < 0.15`, return `"trusted"`. This sits between the `autonomous` check (line 186, `< 0.05`) and the existing `trusted` check (line 188, `< 0.10`).

**Files:**
- Fix: `backend/src/services/risk_assessor.py` (lines 186-195)
- Test: `backend/tests/test_risk_assessor.py` (add new tests)

- [ ] **Step 1: Write failing tests for the edge case**

Add to `backend/tests/test_risk_assessor.py`:

```python
def test_graduate_trust_high_count_moderate_rejections_returns_trusted():
    """H-4: 25+ approved with 10-15% rejection should graduate to trusted."""
    state = SimpleNamespace(
        approved_count=25, rejected_count=3,  # ~10.7% rejection
        cooldown_until=None, trust_level="learning",
    )
    assert graduate_trust(state) == "trusted"


def test_graduate_trust_high_count_high_rejections_stays_learning():
    """H-4: 25+ approved with >=15% rejection stays learning."""
    state = SimpleNamespace(
        approved_count=25, rejected_count=5,  # ~16.7% rejection
        cooldown_until=None, trust_level="learning",
    )
    assert graduate_trust(state) == "learning"
```

- [ ] **Step 2: Fix `graduate_trust()` in `risk_assessor.py`**

Replace lines 186-195 with:

```python
    if state.approved_count >= 25 and rejection_rate < 0.05:
        return "autonomous"
    elif state.approved_count >= 25 and rejection_rate < 0.15:
        # High volume with moderate rejections -- trust earned despite some rejections
        return "trusted"
    elif state.approved_count >= 10 and rejection_rate < 0.10:
        return "trusted"
    elif state.approved_count >= 10 and rejection_rate >= 0.10:
        return "learning"
    elif state.approved_count >= 3 and state.rejected_count == 0:
        return "learning"

    return "first_use"
```

- [ ] **Step 3: Run tests, verify GREEN**

```bash
cd backend && pytest tests/test_risk_assessor.py -v -k "graduate_trust"
```

---

### Task 2: [HIGH] H-14 -- Execution surfaces exclude `awaiting_approval` runs

**Context:** `backend/src/services/surface_builder.py:249-254`. `_build_active_execution_surfaces()` queries `TaskRun.status.in_(["running", "paused"])`. Runs in `"awaiting_approval"` status are invisible on the workspace -- users cannot see execution progress when a step is waiting for their approval. Note that `_build_priority_surfaces()` (line 203-210) does query `awaiting_approval`, but returns an `"alert"` kind surface with no step progress detail.

**Fix:** Add `"awaiting_approval"` to the status filter in `_build_active_execution_surfaces()` so these runs appear as execution surfaces with step progress visible.

**Files:**
- Fix: `backend/src/services/surface_builder.py` (line 253)
- Test: `backend/tests/test_surface_builder.py` (add new test)

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_active_execution_surfaces_include_awaiting_approval(db_factory):
    """H-14: Runs with awaiting_approval should appear in active execution surfaces."""
    # Create a TaskRun with status="awaiting_approval", source="background"
    # Call _build_active_execution_surfaces()
    # Assert the run appears in results with kind="plan"
```

- [ ] **Step 2: Fix the status filter**

In `backend/src/services/surface_builder.py` line 253, change:

```python
# Before
TaskRun.status.in_(["running", "paused"]),

# After
TaskRun.status.in_(["running", "paused", "awaiting_approval"]),
```

- [ ] **Step 3: Run tests, verify GREEN**

```bash
cd backend && pytest tests/test_surface_builder.py -v -k "awaiting_approval"
```

---

### Task 3: [HIGH] H-33 -- `VALID_TRUST_LEVELS` includes `"blocked"` which is not a graduation level

**Context:** `backend/src/api/routes_trust.py:16`. `VALID_TRUST_LEVELS` is used to validate ceiling inputs (lines 137, 186). The set includes `"blocked"` but `TRUST_LEVELS` in `risk_assessor.py:149` is `("first_use", "learning", "trusted", "autonomous")` -- no `"blocked"`. This is correct for ceilings (a user should be able to set a ceiling to `"blocked"` to prevent any auto-execution) but semantically confusing.

**Fix:** Split into two constants: `VALID_CEILING_LEVELS` (includes `"blocked"`) for ceiling validation, and `VALID_TRUST_LEVELS` (excludes `"blocked"`) for anywhere trust graduation levels are validated. Add a comment explaining the distinction. Since ceilings are the only consumer of this set in routes_trust.py, rename the usage sites.

**Files:**
- Fix: `backend/src/api/routes_trust.py` (lines 16, 137, 186)
- Test: `backend/tests/test_routes_trust.py` (add validation test)

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_set_ceiling_rejects_invalid_level(client):
    """H-33: 'blocked' is valid for ceilings but not for trust levels."""
    # Setting ceiling to "blocked" should succeed
    # Setting ceiling to "garbage" should fail with 400
```

- [ ] **Step 2: Split constants in `routes_trust.py`**

Replace line 16:

```python
# Before
VALID_TRUST_LEVELS = {"first_use", "learning", "trusted", "autonomous", "blocked"}

# After
# Graduation levels -- produced by graduate_trust() in risk_assessor.py
VALID_TRUST_LEVELS = {"first_use", "learning", "trusted", "autonomous"}
# Ceiling levels -- "blocked" prevents any auto-execution for a capability
VALID_CEILING_LEVELS = VALID_TRUST_LEVELS | {"blocked"}
```

Update line 137 (`set_trust_ceiling`):
```python
if req.max_level not in VALID_CEILING_LEVELS:
```

Update line 186 (`set_time_policies`):
```python
if p.max_level not in VALID_CEILING_LEVELS:
```

- [ ] **Step 3: Run tests, verify GREEN**

```bash
cd backend && pytest tests/test_routes_trust.py -v
```

---

## Phase 2: MEDIUM Priority Fix

### Task 4: [MEDIUM] M-25 -- `_graduation_progress()` shows 100% AND blocked simultaneously

**Context:** `backend/src/services/trust_engine.py:39-80`. When `blocked_by_rejections` is True, the `percentage` field can still be `1.0` (e.g., 10+ approved at `learning` level with high rejection rate). The frontend would show "100% progress" alongside "blocked by rejections" which is confusing.

**Fix:** When `blocked_by_rejections` is True, cap `percentage` at 0.95 and add a `status` field to the response dict.

**Files:**
- Fix: `backend/src/services/trust_engine.py` (lines 39-80)
- Test: `backend/tests/test_trust_engine.py` (add new test)

- [ ] **Step 1: Write failing tests**

```python
def test_graduation_progress_blocked_caps_percentage():
    """M-25: blocked_by_rejections should cap percentage below 1.0."""
    state = SimpleNamespace(
        trust_level="learning", approved_count=15,
        rejected_count=3,  # 3/18 = 16.7% >= 10%
    )
    progress = _graduation_progress(state)
    assert progress["blocked_by_rejections"] is True
    assert progress["percentage"] < 1.0
    assert progress["status"] == "blocked_by_rejections"


def test_graduation_progress_not_blocked_no_cap():
    """M-25: when not blocked, percentage can reach 1.0."""
    state = SimpleNamespace(
        trust_level="learning", approved_count=15,
        rejected_count=1,  # 1/16 = 6.25% < 10%
    )
    progress = _graduation_progress(state)
    assert progress["blocked_by_rejections"] is False
    assert progress["percentage"] == 1.0
    assert progress.get("status") is None
```

- [ ] **Step 2: Fix `_graduation_progress()` in `trust_engine.py`**

Add at the end of each branch, before the return, and add a post-processing step before the final return of the function. Simpler: add a post-processing block after the existing logic.

Replace the entire function (lines 39-80):

```python
def _graduation_progress(state) -> dict:
    """Compute graduation progress toward the next trust level."""
    level = state.trust_level
    approved = state.approved_count
    rejected = state.rejected_count
    total = approved + rejected

    if level == "first_use":
        result = {
            "next_level": "learning",
            "current": approved,
            "target": 3,
            "percentage": min(approved / 3, 1.0) if approved < 3 else 1.0,
            "blocked_by_rejections": rejected > 0,
        }
    elif level == "learning":
        result = {
            "next_level": "trusted",
            "current": approved,
            "target": 10,
            "percentage": min(approved / 10, 1.0),
            "blocked_by_rejections": (total > 0 and rejected / total >= 0.10),
        }
    elif level == "trusted":
        result = {
            "next_level": "autonomous",
            "current": approved,
            "target": 25,
            "percentage": min(approved / 25, 1.0),
            "blocked_by_rejections": (total > 0 and rejected / total >= 0.05),
        }
    else:
        result = {
            "next_level": None,
            "current": approved,
            "target": approved,
            "percentage": 1.0,
            "blocked_by_rejections": False,
        }

    # Cap percentage when blocked so UI never shows 100% + blocked simultaneously
    if result["blocked_by_rejections"]:
        result["percentage"] = min(result["percentage"], 0.95)
        result["status"] = "blocked_by_rejections"

    return result
```

- [ ] **Step 3: Run tests, verify GREEN**

```bash
cd backend && pytest tests/test_trust_engine.py -v -k "graduation_progress"
```

---

## Phase 3: LOW Priority Fixes

### Task 5: [LOW] L-6 -- `_trust_level_index` duplicated in two files

**Context:** `backend/src/services/risk_assessor.py:149-156` defines `TRUST_LEVELS` tuple and `_trust_level_index()`. `backend/src/services/trust_engine.py:30-36` defines its own `_trust_level_index()` with an inline tuple. Both do the same thing.

**Fix:** Delete `_trust_level_index` from `trust_engine.py` and import from `risk_assessor.py` (which already exports `TRUST_LEVELS`).

**Files:**
- Fix: `backend/src/services/trust_engine.py` (lines 30-36, delete + add import)
- Fix: `backend/src/services/risk_assessor.py` (no change -- already canonical)

- [ ] **Step 1: Update imports in `trust_engine.py`**

In `trust_engine.py`, line 21-25, add `_trust_level_index` to the existing import:

```python
# Before
from src.services.risk_assessor import (
    RiskAssessment,
    get_or_create_trust_state,
    min_trust_level,
)

# After
from src.services.risk_assessor import (
    RiskAssessment,
    _trust_level_index,
    get_or_create_trust_state,
    min_trust_level,
)
```

- [ ] **Step 2: Delete the duplicate function (lines 30-36)**

Remove `_trust_level_index` from `trust_engine.py`.

- [ ] **Step 3: Run full trust test suite**

```bash
cd backend && pytest tests/test_trust_engine.py tests/test_risk_assessor.py -v
```

---

### Task 6: [LOW] L-7 -- `set_ceilings_batch()` sequential N queries

**Context:** `backend/src/services/trust_engine.py:288-294`. Loops over capabilities calling `set_ceiling()` one at a time, each issuing a SELECT + possible INSERT + flush.

**Fix:** Batch the flushes -- accumulate changes and flush once at the end instead of per-capability.

**Files:**
- Fix: `backend/src/services/trust_engine.py` (lines 288-294)

- [ ] **Step 1: Refactor `set_ceilings_batch()`**

```python
async def set_ceilings_batch(self, capabilities: list[str], max_level: str) -> int:
    """Batch-set ceilings for multiple capabilities. Returns count updated."""
    from src.models.trust_state import TrustCeiling

    # Load all existing ceilings in one query
    result = await self._db.execute(
        select(TrustCeiling).where(
            TrustCeiling.workspace_id == self._workspace_id,
            TrustCeiling.capability.in_(capabilities),
        )
    )
    existing = {c.capability: c for c in result.scalars().all()}

    for cap in capabilities:
        if cap in existing:
            existing[cap].max_level = max_level
        else:
            self._db.add(
                TrustCeiling(
                    workspace_id=self._workspace_id,
                    capability=cap,
                    max_level=max_level,
                )
            )

    await self._db.flush()
    return len(capabilities)
```

- [ ] **Step 2: Run tests**

```bash
cd backend && pytest tests/test_trust_engine.py -v -k "ceiling"
```

---

### Task 7: [LOW] L-8 -- `PolicyDecision` Literal includes `auto_execute` never produced by TrustEngine

**Context:** `backend/src/orchestrator/contracts.py:187-193`. The `decision` Literal includes `"auto_execute"` which is only produced by the Governor's `AUTO_EXECUTE_DECISIONS` set, never by `TrustEngine._matrix_lookup()` (which produces `"auto_execute_notify"`, `"auto_execute_silent"`, `"approval_required"`).

**Fix:** Add a clarifying comment. No code change needed -- the Literal is correct as a union type for all producers.

**Files:**
- Fix: `backend/src/orchestrator/contracts.py` (line 187)

- [ ] **Step 1: Add documentation comment**

```python
    # Produced by: Governor (auto_execute, blocked), TrustEngine (auto_execute_notify,
    # auto_execute_silent, approval_required). Union of all producers.
    decision: Literal[
        "auto_execute",
        "auto_execute_notify",
        "auto_execute_silent",
        "approval_required",
        "blocked",
    ]
```

---

## Verification

After all tasks complete:

```bash
cd backend && pytest tests/ -v -k "trust or risk_assessor or surface_builder or routes_trust" --tb=short
cd backend && ruff check src/services/risk_assessor.py src/services/trust_engine.py src/services/surface_builder.py src/api/routes_trust.py src/orchestrator/contracts.py
```
