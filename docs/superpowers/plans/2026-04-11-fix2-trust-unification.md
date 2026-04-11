# Fix-2: Trust Path Unification

**Priority:** P0 — trust graduation is completely broken
**Risk:** Medium — architectural changes across governor, trust_engine, graph_executor, routes_approvals
**Estimated files:** ~6-8
**Dependencies:** None (can run in parallel with Fix-1)

## Overview

Trust evaluation currently flows through 3 disconnected paths that never converge:

1. **Governor plan-level** (`governor.py:_check_trust`): Calls `TrustEngine.should_auto_approve()` — a method that does not exist. Silently returns `False` on every call due to try/except. Trust graduation at plan level is permanently broken.

2. **GraphExecutor step-level** (`graph_executor.py:_execute_step`): Correctly calls `TrustEngine.evaluate(capability, risk_assessment)`, but mutates `self._trust_engine._workspace_id` on shared singleton (line 601), creating cross-run contamination. When TrustEngine is absent, falls into legacy `requires_approval` fallback that never contributes to graduation.

3. **Approval-resume path** (`routes_approvals.py:222-303`): Creates executors via `create_graph_executor()` without injecting `trust_engine` or `redis`, so resumed runs always fall into the legacy fallback. Tool-level resume (line 253-303) creates Plan + TaskRun but never populates steps, so the DAG loop completes immediately with no work done.

**Target state:** A single trust evaluation path where:
- Governor delegates to TrustEngine for plan-level decisions (using `evaluate()`)
- GraphExecutor passes `workspace_id` as a parameter (no mutation)
- Approval-resume injects TrustEngine + Redis into executors
- All approval records store actual capability strings (not risk levels)
- Risk cache keys include user context to prevent cross-user pollution

## Phase 1: Fix TrustEngine API Surface

### Task 1.1: Add workspace_id parameter to TrustEngine.evaluate()

**File:** `backend/src/services/trust_engine.py`

Currently `TrustEngine.__init__` takes `workspace_id` (line 86-88) and `evaluate()` reads `self._workspace_id` implicitly via `_get_trust_state` (line 93) and `_get_ceiling` (line 94).

Change `evaluate()` signature to accept an optional `workspace_id` override parameter:

```python
async def evaluate(
    self, capability: str, risk_assessment: RiskAssessment, workspace_id: str | None = None
) -> PolicyDecision:
```

When `workspace_id` is provided, use it instead of `self._workspace_id`. Thread it through `_get_trust_state` and `_get_ceiling` as well. This eliminates the need to mutate `self._workspace_id` from callers.

**Affected methods:**
- `evaluate()` (line 90): Add `workspace_id` param, pass to helpers
- `_get_trust_state()` (line 124): Add `workspace_id` param, use it over `self._workspace_id`
- `_get_ceiling()` (line 128): Add `workspace_id` param, use it over `self._workspace_id`

### Task 1.2: Add plan-level convenience method to TrustEngine

**File:** `backend/src/services/trust_engine.py`

Governor needs a plan-level trust check. Add a method that takes a plan's risk level string and a capability string, constructs a minimal `RiskAssessment`, and calls `evaluate()`:

```python
async def evaluate_plan_risk(
    self, capability: str, risk_level: str, workspace_id: str | None = None
) -> PolicyDecision:
    """Convenience: evaluate trust using a static risk level (no LLM call)."""
    assessment = RiskAssessment(
        risk_level=risk_level,
        reasoning=f"Plan-level risk: {risk_level}",
    )
    return await self.evaluate(capability, assessment, workspace_id=workspace_id)
```

This replaces the non-existent `should_auto_approve()` with the real TrustEngine evaluation pipeline.

## Phase 2: Fix Governor Integration

### Task 2.1: Replace _check_trust with TrustEngine.evaluate_plan_risk

**File:** `backend/src/services/governor.py`

**Current (broken):** Lines 221-229 — `_check_trust` calls `self._trust_engine.should_auto_approve(user_id, action_type, risk_level)` which does not exist on TrustEngine. The try/except silently returns `False`.

**Fix:** Replace `_check_trust` with a call to `TrustEngine.evaluate_plan_risk()`. The Governor's `_apply_policy` (line 231) calls `_check_trust` in two places:
- Line 260: medium-risk plans — `await self._check_trust(user_id, "write", risk)`
- Line 265: low-risk plans — `await self._check_trust(user_id, "read", risk)`

Replace both with:

```python
async def _check_trust(self, workspace_id: str, capability: str, risk_level: str) -> bool:
    """Check if TrustEngine recommends auto-execution for a plan."""
    if not self._trust_engine:
        return False
    try:
        decision = await self._trust_engine.evaluate_plan_risk(
            capability=capability,
            risk_level=risk_level,
            workspace_id=workspace_id,
        )
        return decision.decision in ("auto_execute", "auto_execute_notify", "auto_execute_silent")
    except Exception:
        logger.warning("Trust engine check failed", exc_info=True)
        return False
```

Update `_apply_policy` to pass `workspace_id` (available from `evaluate_plan`) and a meaningful capability string. Since plans don't have a single capability, use the first task's capability or a generic `"plan_execution"` fallback.

**Signature change for `_apply_policy`:**
```python
async def _apply_policy(self, plan: Plan, user_id: str, workspace_id: str = "") -> str:
```

Update `evaluate_plan` (line 83) to pass `workspace_id` through:
```python
policy_decision = await self._apply_policy(plan, user_id, workspace_id)
```

### Task 2.2: Fix approval_type — pass capability, not risk level

**File:** `backend/src/services/governor.py`

**Current (broken):** Line 189 — `approval_type=plan.risk_level or "medium"` stores `"medium"` or `"high"` as the capability in TrustState, making graduation records meaningless.

**Fix:** Extract the capability from the plan's first task (or use `"plan_execution"` as fallback):

```python
# Before _create_approval call, extract capability
first_task_cap = "plan_execution"
if plan.tasks:
    first_task_cap = (
        plan.tasks[0].task_type
        or (plan.tasks[0].input_data or {}).get("capability", "plan_execution")
    )

approval = await create_approval(
    ...
    approval_type=first_task_cap,
    ...
)
```

### Task 2.3: Fix TaskRun status — map approval_required to awaiting_approval

**File:** `backend/src/services/governor.py`

**Current (broken):** Line 97 — `status="pending" if policy_decision == "auto_execute" else policy_decision`. When `policy_decision = "approval_required"`, this sets `TaskRun.status = "approval_required"` which is NOT a valid TaskRun status.

Line 117 later overwrites it to `"awaiting_approval"`, but there's a window where the invalid status is flushed. More importantly, `auto_execute_notify` and `auto_execute_silent` (from TrustEngine) would also be stored as status, which are also invalid.

**Fix:** Map all PolicyDecision values to valid TaskRun statuses:

```python
_DECISION_TO_RUN_STATUS = {
    "auto_execute": "pending",
    "auto_execute_notify": "pending",
    "auto_execute_silent": "pending",
    "approval_required": "awaiting_approval",
    "blocked": "cancelled",
}

run = TaskRun(
    ...
    status=_DECISION_TO_RUN_STATUS.get(policy_decision, "pending"),
)
```

Remove the separate `run.status = "awaiting_approval"` on line 117 (now redundant) and the `run.status = "cancelled"` on line 142 (handled by the mapping for `"blocked"`). Keep the `plan.status = "blocked"` assignment.

## Phase 3: Fix Approval Resume Path

### Task 3.1: Inject TrustEngine and Redis into approval-route executors

**File:** `backend/src/api/routes_approvals.py`

**Current (broken):** Lines 223-225, 247-249, 378-380 — `create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)` does not pass `trust_engine` or `redis`. The `create_graph_executor` factory (graph_executor.py:39-135) does not create these either.

**Fix option A (preferred):** Add `trust_engine` and `redis` creation to `create_graph_executor` factory, matching how the orchestrator provides them. Add these to the factory function:

```python
async def create_graph_executor(
    settings: Settings,
    db: AsyncSession,
    workspace_id: str = "",
    db_factory=None,
    execute_tool_fn=None,
    budget=None,
    circuit_breaker=None,
) -> GraphExecutor:
    # ... existing code ...

    # Add trust engine
    trust_engine = None
    try:
        from src.services.trust_engine import TrustEngine
        trust_engine = TrustEngine(db, workspace_id)
    except Exception:
        logger.debug("TrustEngine unavailable for GraphExecutor", exc_info=True)

    # Add Redis
    redis_conn = None
    try:
        import redis.asyncio as aioredis
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        logger.debug("Redis unavailable for GraphExecutor", exc_info=True)

    return GraphExecutor(
        ...
        trust_engine=trust_engine,
        redis=redis_conn,
    )
```

This fixes all 3 callsites in routes_approvals.py without changing them.

### Task 3.2: Fix tool-level approval resume — populate steps

**File:** `backend/src/api/routes_approvals.py`

**Current (broken):** Lines 253-303 — Creates `Plan` + `PlanTask` + `TaskRun` but never calls `executor.populate_run_steps()`. The DAG loop finds no steps and completes immediately.

**Fix:** After creating the Plan and TaskRun, create an executor and populate steps before committing:

```python
db.add(bg_run)
await db.flush()

# Populate steps from plan tasks
executor = await create_graph_executor(
    settings=settings, db=db, workspace_id=workspace_id
)
await executor.populate_run_steps(bg_run.run_id, plan_id)
await db.commit()
```

The scheduler's `_tick_background_tasks` will pick up the run (it queries `status="pending"` and `source="background"`), but this run has `source="approval_resume"`. Either:
- Change `source` to `"background"` so the scheduler picks it up, OR
- Execute immediately after populating steps: `await executor.execute_run(bg_run.run_id)`

The immediate execution approach is better since the user just approved and expects action.

## Phase 4: Fix Data Integrity Issues

### Task 4.1: Include user_id in risk cache key

**File:** `backend/src/services/risk_assessor.py`

**Current (broken):** Lines 60-63 — `build_risk_cache_key` hashes only `capability + step_input`. Two users in the same workspace with different contexts get the same cached risk assessment.

**Fix:** Add `user_context` to the hash:

```python
def build_risk_cache_key(capability: str, step_input: dict, user_context: dict | None = None) -> str:
    """Build a deterministic cache key from capability + step input + user context."""
    raw = json.dumps(
        {"capability": capability, "input": step_input, "user_context": user_context or {}},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
```

Update the single caller `get_or_assess_risk` (line 120) to pass `user_context`:

```python
cache_key = build_risk_cache_key(capability, step_input, user_context)
```

### Task 4.2: Remove workspace_id mutation in GraphExecutor

**File:** `backend/src/services/graph_executor.py`

**Current (broken):** Line 601 — `self._trust_engine._workspace_id = run.workspace_id or ""` mutates shared TrustEngine state.

**Fix:** After Task 1.1, pass `workspace_id` directly to `evaluate()`:

```python
# Line 603: was self._trust_engine.evaluate(capability, risk)
decision = await self._trust_engine.evaluate(
    capability, risk, workspace_id=run.workspace_id or ""
)
```

Delete line 601 entirely.

## Phase 5: Tests

### Task 5.1: Test Governor uses TrustEngine.evaluate_plan_risk

- Verify `_check_trust` calls `evaluate_plan_risk` with correct params
- Verify plan-level graduation works (mock TrustEngine returning `auto_execute_notify`)
- Verify `approval_type` stores actual capability, not risk level string

### Task 5.2: Test approval-resume creates executor with TrustEngine

- Verify `create_graph_executor` now returns an executor with `_trust_engine` set
- Verify tool-level resume populates steps and executes

### Task 5.3: Test risk cache key includes user context

- Verify different `user_context` dicts produce different cache keys
- Verify same capability + input + different user = cache miss

### Task 5.4: Test workspace_id passed as parameter, not mutated

- Verify `TrustEngine.evaluate()` accepts and uses `workspace_id` param
- Verify `_workspace_id` attribute is not mutated by GraphExecutor

### Task 5.5: Test TaskRun status mapping

- Verify `auto_execute` → `"pending"`
- Verify `auto_execute_notify` → `"pending"`
- Verify `approval_required` → `"awaiting_approval"`
- Verify `blocked` → `"cancelled"`

## Verification

- [ ] `ruff check src/services/governor.py src/services/trust_engine.py src/services/graph_executor.py src/services/risk_assessor.py src/api/routes_approvals.py`
- [ ] `pytest tests/ -v -k "trust or governor or approval"` — all existing tests pass
- [ ] `pytest tests/ -v` — full suite, no regressions
- [ ] Governor with TrustEngine: medium-risk plan with graduated trust → `auto_execute`
- [ ] Governor without TrustEngine: medium-risk plan → `approval_required` (safe fallback)
- [ ] Approval resume: executor has TrustEngine injected, step-level trust works
- [ ] Tool-level resume: steps are populated, run executes to completion
- [ ] Risk cache: different users get different cache entries
- [ ] No `_workspace_id` mutation anywhere in graph_executor.py
- [ ] TaskRun.status is always a valid status enum value after Governor.evaluate_plan

## File Change Summary

| File | Changes |
|------|---------|
| `backend/src/services/trust_engine.py` | Add `workspace_id` param to `evaluate()`, `_get_trust_state()`, `_get_ceiling()`. Add `evaluate_plan_risk()` convenience method. |
| `backend/src/services/governor.py` | Rewrite `_check_trust` to use `evaluate_plan_risk`. Fix `_apply_policy` to pass `workspace_id`. Fix `approval_type` to use capability. Add `_DECISION_TO_RUN_STATUS` mapping. |
| `backend/src/services/graph_executor.py` | Remove line 601 (`_workspace_id` mutation). Pass `workspace_id` to `evaluate()`. Add TrustEngine + Redis creation to `create_graph_executor`. |
| `backend/src/services/risk_assessor.py` | Add `user_context` to `build_risk_cache_key`. Update `get_or_assess_risk` caller. |
| `backend/src/api/routes_approvals.py` | Add `populate_run_steps()` + `execute_run()` to tool-level resume path (lines 253-303). |
| `backend/tests/test_trust_unification.py` | New: tests for Tasks 5.1-5.5. |
