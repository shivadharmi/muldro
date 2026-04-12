# Execution System Hardening — Spec A

**Date:** 2026-04-13
**Branch:** `improve-surface-design-v1`
**Scope:** 20 surgical code fixes for correctness, safety, and observability in the plan execution pipeline. No new files, no architecture changes, no migrations.

---

## Context

A deep-dive audit of the plan-execution system (documented in `docs/architecture/plan-execution-deep-dive.md` and `docs/gap-analysis-plan-execution-system.md`) identified 20 issues across critical, high, and medium severity. All are logic bugs, race conditions, missing handlers, or silent failures in existing code.

This spec covers **Spec A — Code Fixes**. A follow-up **Spec B** will address feature gaps (plan history API, run cancel/retry endpoints, execution history page, status audit trail, RuntimeEvent standardization).

---

## Delivery Strategy

Two PRs, ordered by severity:

| PR | Scope | Fixes | Files | Est. Lines |
|----|-------|-------|-------|------------|
| **PR 1** | Critical + High | #1–13 | 8 source + 2 docs | ~350–450 |
| **PR 2** | Medium | #14–20 | 5 source + 1 doc | ~150–200 |

PR 1 ships first. PR 2 follows once PR 1 merges.

---

## PR 1: Critical + High Fixes

### Fix 1 — DLQ retry actually re-executes operations [CRITICAL]

**File:** `backend/src/services/scheduler.py` — `_tick_dlq_retry()`
**Problem:** `mark_retrying()` increments the attempt counter but never dispatches the failed operation. Dead-lettered tasks accumulate forever.

**Change:**

Add a dispatch table mapping `operation_type` to a handler function. After `mark_retrying()` succeeds, call the handler with the stored payload:

```python
DLQ_HANDLERS = {
    "background_task": self._retry_background_task,
    "embedding": self._retry_embedding,
    "event_processing": self._retry_event_processing,
}

for entry in pending:
    if not await dlq.mark_retrying(entry.entry_id):
        continue  # exhausted
    handler = DLQ_HANDLERS.get(entry.operation_type)
    if handler:
        try:
            await handler(entry.payload, entry.user_id, entry.workspace_id)
            await dlq.mark_resolved(entry.entry_id)
        except Exception:
            logger.warning("DLQ retry failed: %s", entry.entry_id, exc_info=True)
    else:
        logger.warning("No DLQ handler for operation_type=%s", entry.operation_type)
```

Individual retry handlers are thin wrappers:
- `_retry_background_task`: transitions the linked TaskRun back to `pending` (scheduler's next `_tick_background_tasks` picks it up naturally)
- `_retry_embedding`: calls `EmbeddingService.embed_and_store()` with the payload
- `_retry_event_processing`: calls `EventProcessor.process()` with the payload

**Test:** Verify that after `mark_retrying()`, the handler is called and on success `mark_resolved()` transitions to `resolved`. Verify unknown `operation_type` logs a warning.

---

### Fix 2 — Background task pickup uses row locking [CRITICAL]

**File:** `backend/src/services/scheduler.py` — `_tick_background_tasks()`
**Problem:** `SELECT` without `FOR UPDATE` allows concurrent scheduler ticks (or instances) to pick up the same TaskRun and execute it twice. Duplicate external actions (emails, calendar events).

**Change:**

Add `with_for_update(skip_locked=True)` to the query:

```python
result = await db.execute(
    select(TaskRun)
    .where(
        TaskRun.status == "pending",
        TaskRun.source.in_(["background", "approval_resume"]),
    )
    .order_by(TaskRun.created_at.asc())
    .limit(3)
    .with_for_update(skip_locked=True)
)
```

`SKIP LOCKED` is the standard Postgres pattern for worker-queue dequeue — locked rows are skipped (not waited on), preventing both duplicate pickup and deadlocks.

**Test:** Verify that two concurrent `_tick_background_tasks` calls on overlapping pending runs do not both pick up the same run. Mock `db.execute` to confirm `with_for_update` clause is present.

---

### Fix 3 — WebSocket approval payload key mismatch [CRITICAL]

**File:** `backend/src/api/routes_ws.py` — approve/reject/edit handlers
**Problem:** Frontend sends `{approval_id: "apr_xxx"}`, backend reads `payload.get("id")`. Approval ID is always empty string, causing 404 on every WebSocket approval.

**Change:**

In all three WS action handlers (`_handle_approve`, `_handle_reject`, and the new `_handle_edit_before_approve`), accept both key names:

```python
approval_id = payload.get("approval_id") or payload.get("id", "")
```

This covers the current frontend and any older clients using `"id"`.

**Test:** Verify that `_handle_approve` with `{"approval_id": "apr_xxx"}` correctly resolves the approval ID. Verify fallback to `{"id": "apr_xxx"}` also works.

---

### Fix 4 — Resume failure after approval surfaces error [CRITICAL]

**File:** `backend/src/api/routes_approvals.py` — all three approval types
**Problem:** `resume_run()` exceptions are caught and logged, but the endpoint returns 200 OK. User sees "approved" but execution never resumes. Run stuck forever.

**Change:**

On resume failure, mark the run as failed and include the error in the response:

```python
try:
    await executor.resume_run(run_id)
except Exception as exc:
    logger.exception("Resume failed after approval: %s", run_id)
    async with db_factory() as fail_db:
        run = await fail_db.get(TaskRun, run_id)
        if run and run.status not in ("failed", "completed", "cancelled"):
            transition_run(run, "failed")
            run.error = {"type": "resume_failure", "message": str(exc)[:500]}
            await fail_db.commit()
    return ApprovalResponse(
        approval_id=approval_id,
        status="approved",
        resume_failed=True,
        resume_error=str(exc)[:200],
    )
```

Apply this pattern to all three approval handlers (step-level, plan-level, tool-level). The approval itself succeeded (status is "approved"), but the `resume_failed` flag tells the frontend to show an error.

**Schema change:** Add `resume_failed: bool = False` and `resume_error: str | None = None` fields to the `ApprovalResponse` Pydantic model in `routes_approvals.py`.

**Test:** Verify that when `resume_run()` raises, the run transitions to `failed` and the response includes `resume_failed=True`. Verify double-failure (run already failed) doesn't raise.

---

### Fix 5 — Fast-path steps get step IDs [CRITICAL]

**File:** `backend/src/orchestrator/intent_classifier.py` — `intent_to_plan()`
**Problem:** `PlanStep` objects created with `step_id=""`. Downstream `_persist_plan_record()` skips empty IDs during dependency resolution.

**Change:**

Generate sequential step IDs:

```python
steps = [
    PlanStep(
        step_id=f"s{i + 1}",
        description=desc,
        capability=cap,
        ...
    )
    for i, (desc, cap) in enumerate(planned_steps)
]
```

**Test:** Verify that `intent_to_plan("greeting", "hello", [])` returns a PlanOutput with `steps[0].step_id == "s1"`. Verify all 10 fast intent types produce non-empty step IDs.

---

### Fix 6 — Step reference resolution logs on failure [HIGH]

**File:** `backend/src/services/graph_executor.py` — `_resolve_step_references()`
**Problem:** If `{task_id}.output.field` can't resolve, the literal string passes through silently. Downstream agent gets garbage input.

**Change:**

Add warning logs when task_id is not found or field is missing:

```python
def resolve(value):
    if isinstance(value, str) and value.startswith("{") and "}.output." in value:
        ref, _, field = value[1:].partition("}.output.")
        source = outputs_by_task.get(ref)
        if source is None:
            logger.warning(
                "Step reference unresolved: task '%s' not found in completed steps "
                "(run_id=%s, step=%s)",
                ref, run_id, step.step_id,
            )
            return value
        if isinstance(source, dict) and field not in source:
            logger.warning(
                "Step reference field missing: '%s' not in task '%s' output "
                "(run_id=%s, step=%s, available_keys=%s)",
                field, ref, run_id, step.step_id, list(source.keys()),
            )
            return value
        if isinstance(source, dict):
            return source.get(field, value)
    return value
```

Also add a post-resolution summary warning:

```python
resolved = {k: resolve(v) for k, v in input_data.items()}
unresolved = [k for k, v in resolved.items() if isinstance(v, str) and "}.output." in v]
if unresolved:
    logger.warning("Step %s has %d unresolved references: %s", step.step_id, len(unresolved), unresolved)
return resolved
```

**Test:** Verify that resolving `{missing_task}.output.field` returns the original string and logs a warning. Verify successful resolution still works.

---

### Fix 7 — Step retry adds exponential backoff [HIGH]

**File:** `backend/src/services/graph_executor.py` — step failure handling
**Problem:** Failed steps immediately retry with no delay, hammering the same endpoint.

**Change:**

Add backoff delay before transitioning back to `pending`:

```python
if step.retry_count < step.max_retries:
    step.retry_count += 1
    delay = min(2 ** step.retry_count, 30)  # 2s, 4s, 8s, 16s, 30s cap
    logger.info(
        "Step %s retry %d/%d after %.0fs backoff",
        step.step_id, step.retry_count, step.max_retries, delay,
    )
    await asyncio.sleep(delay)
    transition_step(step, "pending")
```

The `asyncio.sleep` runs within the DAG loop. The run is in `running` status and the sleep doesn't hold a DB lock (the flush happens after the transition).

**Test:** Verify that after first failure, step waits 2s before retrying. Verify delay caps at 30s for high retry counts.

---

### Fix 8 — Forward dependencies use two-pass resolution [HIGH]

**File:** `backend/src/orchestrator/jarvis.py` — `_persist_plan_record()`
**Problem:** Single-pass iteration drops forward references. If step s2 depends on s3 (declared later), the dependency is silently lost.

**Change:**

Split into two passes:

```python
# Pass 1: Build complete step_id -> task_id mapping
step_to_task: dict[str, str] = {}
for step in plan_output.steps:
    if not step.step_id:
        continue
    task_id = f"ptask_{ulid.new()}"
    step_to_task[step.step_id] = task_id

# Pass 2: Create PlanTask rows with fully resolved dependencies
tasks: list[PlanTask] = []
for step in plan_output.steps:
    if not step.step_id:
        continue
    resolved_deps = []
    for dep_step_id in step.depends_on:
        dep_task_id = step_to_task.get(dep_step_id)
        if dep_task_id:
            resolved_deps.append(dep_task_id)
        else:
            logger.warning(
                "Plan %s: step '%s' depends on unknown step '%s' — skipping dependency",
                plan_id, step.step_id, dep_step_id,
            )
    # PR1: only persist jarvis-actor steps (user steps added in Fix 16 / PR2)
    if step.actor != "jarvis":
        continue
    tasks.append(PlanTask(
        task_id=step_to_task[step.step_id],
        plan_id=plan_id,
        workspace_id=workspace_id,
        task_type=step.capability,
        input_data=step.input if step.input else None,
        depends_on=resolved_deps if resolved_deps else None,
        status="pending",
    ))
```

Steps without IDs are logged and skipped rather than silently creating orphaned tasks. User-actor steps are deferred to Fix 16 (PR2).

**Test:** Verify that a plan with `s2.depends_on=["s3"]` where s3 is declared after s2 correctly resolves the dependency. Verify unknown step references log a warning.

---

### Fix 9 — Planner JSON parse failure logs warning [HIGH]

**File:** `backend/src/orchestrator/intent_classifier.py` — `extract_plan()`
**Problem:** When Planner response isn't valid JSON, a minimal fallback plan is returned silently. Complex multi-step plans degrade to a single "respond" step with no indication.

**Change:**

Add a warning log and signal degradation via `achievable="partial"`:

```python
# After all parse attempts fail:
logger.warning(
    "Planner response could not be parsed as PlanOutput — "
    "falling back to single respond step. Raw response (first 500 chars): %s",
    response_text[:500],
)
return PlanOutput(
    goal=response_text[:200],
    steps=[PlanStep(step_id="s1", description="Respond to user", capability="respond")],
    achievable="partial",
)
```

**Test:** Verify that unparseable Planner output triggers the warning log and returns `achievable="partial"`.

---

### Fix 10 — Trust graduation uses pessimistic locking [HIGH]

**File:** `backend/src/services/risk_assessor.py` — `record_approval_decision()`
**Problem:** Concurrent approvals for the same capability read stale `TrustState`, causing lost counter updates. Trust may never graduate.

**Change:**

Use `SELECT ... FOR UPDATE` when fetching the TrustState row:

```python
result = await db.execute(
    select(TrustState)
    .where(
        TrustState.workspace_id == workspace_id,
        TrustState.capability == capability,
    )
    .with_for_update()
)
state = result.scalar_one_or_none()
```

The `FOR UPDATE` lock is held only for the transaction duration (typically <50ms). No deadlock risk — single row locked.

If `state` is None, create a new one and flush before incrementing. Existing graduation/demotion logic is unchanged.

**Test:** Verify that two concurrent `record_approval_decision()` calls for the same capability both increment correctly (final count = initial + 2). Mock to confirm `with_for_update` is present.

---

### Fix 11 — Missing `edit_before_approve` WebSocket handler [HIGH]

**File:** `backend/src/api/routes_ws.py`
**Problem:** Frontend Edit button sends `edit_before_approve` action, but no handler is registered. Button does nothing.

**Change:**

Add handler that saves edits to the approval's `artifact_refs`:

```python
async def _handle_edit_before_approve(
    ws, payload, user_id, workspace_id, db_factory, settings,
) -> dict:
    approval_id = payload.get("approval_id") or payload.get("id", "")
    edits = payload.get("edits", {})
    if not approval_id:
        return {"status": "error", "message": "Missing approval_id"}

    async with db_factory() as db:
        result = await db.execute(
            select(Approval).where(
                Approval.approval_id == approval_id,
                Approval.user_id == user_id,
            )
        )
        approval = result.scalar_one_or_none()
        if not approval or approval.status != "pending":
            return {"status": "error", "message": "Approval not found or not pending"}

        if approval.artifact_refs and edits:
            merged = {**(approval.artifact_refs or {}), **edits}
            approval.artifact_refs = merged
        approval.decision_reason = "edited_before_approve"
        await db.commit()

    return {"status": "success", "approval_id": approval_id, "message": "Edits saved"}
```

Register: `ACTION_HANDLERS["edit_before_approve"] = _handle_edit_before_approve`

This saves edits only — user still clicks Approve separately to resume execution with modified params.

**Test:** Verify that sending `{"action": "edit_before_approve", "approval_id": "apr_xxx", "edits": {"to": "new@example.com"}}` updates `artifact_refs`. Verify non-pending approval returns error.

---

### Fix 12 — Sequential execution documented honestly [HIGH]

**Files:** `docs/architecture/plan-execution-deep-dive.md`, `docs/architecture/execution.md`
**Problem:** Docs claim parallel execution via `asyncio.gather()`. Code runs steps sequentially.

**Change:**

Update DAG Resolution sections in both documents:

```markdown
Ready steps are executed **sequentially** within each batch. While the DAG
identifies independent steps that could run concurrently, the current
implementation serializes them because SQLAlchemy's AsyncSession is not safe
for concurrent coroutines sharing the same session.

Future optimization: use a separate DB session per parallel step branch.
```

Remove or correct any references to `asyncio.gather()` for step execution.

---

### Fix 13 — Approval idempotency for double-click [HIGH]

**File:** `backend/src/api/routes_approvals.py`
**Problem:** Double-clicking Approve returns 400 on the second click even though the first succeeded.

**Change:**

Check status before rejecting. If already approved/rejected, return success:

```python
if approval.status == "approved":
    return ApprovalResponse(
        approval_id=approval_id,
        status="approved",
        already_resolved=True,
    )

if approval.status == "expired":
    raise HTTPException(status_code=410, detail="Approval has expired")
```

**Schema change:** Add `already_resolved: bool = False` field to the `ApprovalResponse` Pydantic model in `routes_approvals.py`.

Same pattern for `reject_action`.

**Test:** Verify that calling `approve_action` twice returns success both times. Second call has `already_resolved=True`.

---

## PR 2: Medium Fixes

### Fix 14 — Documentation type mismatch for `achievable`

**File:** `docs/architecture/message-flow.md`
**Change:** Update `achievable: bool` to `achievable: Literal["full", "partial", "not_achievable"] = "full"` in the PlanOutput contract snippet.

---

### Fix 15 — Memory writeback failure logged at WARNING

**File:** `backend/src/services/graph_executor.py` — `_writeback_memories()`
**Change:** Escalate `logger.debug` to `logger.warning` and include `run.run_id` for traceability.

---

### Fix 16 — User actor steps persisted as PlanTask rows

**File:** `backend/src/orchestrator/jarvis.py` — `_persist_plan_record()`
**Change:** Remove the `if step.actor != "jarvis": continue` guard in Pass 2 (introduced in Fix 8). For user steps, set `task_type="user_action"` and `status="awaiting_input"` instead of the jarvis defaults. GraphExecutor already handles `awaiting_input` status — it pauses the run and waits for user response.

---

### Fix 17 — PlanOutput fields stored in Plan table

**File:** `backend/src/orchestrator/jarvis.py` — `_persist_plan_record()`
**Change:** Populate the existing `plan_output_json` column: `plan_output_json=plan_output.model_dump(mode="json")`. No migration needed — column already exists.

---

### Fix 18 — System capability steps get audit records

**File:** `backend/src/orchestrator/jarvis.py` — `_handle_system_capability()`
**Change:** After execution, create an `InteractionLog` entry with `interaction_type=step.capability` and metadata containing plan_step and actor.

---

### Fix 19 — PlanOutput validator checks step_id uniqueness

**File:** `backend/src/orchestrator/contracts.py` — `_validate_step_dependencies()`
**Change:** Add duplicate step_id check at the top of the existing validator. Raise `ValueError` on duplicates.

---

### Fix 20 — Long DAG session management warning

**File:** `backend/src/services/graph_executor.py` — `_execute_dag()`
**Change:** Add elapsed time tracking. Log a warning if DAG execution exceeds 120 seconds, recommending the `db_factory` pattern. Actual per-step session refactor deferred to Spec B.

---

## Dependency Order

```
Phase 1 (independent, can parallelize):
  Fix 5  — fast-path step IDs (intent_classifier.py)
  Fix 6  — step reference logging (graph_executor.py)
  Fix 7  — step retry backoff (graph_executor.py)
  Fix 9  — parse failure warning (intent_classifier.py)
  Fix 12 — sequential docs (2 doc files)
  Fix 19 — step_id uniqueness (contracts.py)

Phase 2 (depend on Phase 1):
  Fix 8  — two-pass deps (jarvis.py) — uses step IDs from Fix 5
  Fix 10 — trust locking (risk_assessor.py)

Phase 3 (depend on Phase 2):
  Fix 1  — DLQ retry dispatch (scheduler.py)
  Fix 2  — background task locking (scheduler.py)
  Fix 3  — WS approval key (routes_ws.py)
  Fix 4  — resume error handling (routes_approvals.py)
  Fix 11 — edit_before_approve (routes_ws.py) — uses key fix from Fix 3
  Fix 13 — approval idempotency (routes_approvals.py)

Phase 4 (medium, after PR 1 merges):
  Fix 14 — achievable doc (message-flow.md)
  Fix 15 — writeback warning (graph_executor.py)
  Fix 16 — user actor steps (jarvis.py) — builds on Fix 8
  Fix 17 — plan_output_json (jarvis.py)
  Fix 18 — system step audit (jarvis.py)
  Fix 20 — DAG session warning (graph_executor.py)
```

## Files Changed Summary

| File | Fixes | PR |
|------|-------|----|
| `backend/src/services/scheduler.py` | #1, #2 | 1 |
| `backend/src/services/graph_executor.py` | #6, #7, #15, #20 | 1+2 |
| `backend/src/services/risk_assessor.py` | #10 | 1 |
| `backend/src/api/routes_ws.py` | #3, #11 | 1 |
| `backend/src/api/routes_approvals.py` | #4, #13 | 1 |
| `backend/src/orchestrator/intent_classifier.py` | #5, #9 | 1 |
| `backend/src/orchestrator/jarvis.py` | #8, #16, #17, #18 | 1+2 |
| `backend/src/orchestrator/contracts.py` | #19 | 2 |
| `docs/architecture/plan-execution-deep-dive.md` | #12 | 1 |
| `docs/architecture/execution.md` | #12 | 1 |
| `docs/architecture/message-flow.md` | #14 | 2 |

## Testing Strategy

- Each fix includes a unit test verifying the fix and regression safety
- Run full test suite before each PR: `pytest tests/ -v`
- Run linter: `ruff check src/ tests/` and `ruff format src/ tests/`
- Manual verification of WS approval flow (Fix 3, 11) via browser

## Out of Scope (Deferred to Spec B)

- Plan history API (`GET /v1/plans`)
- Run cancel/retry API endpoints
- Execution history frontend page
- Status transition audit trail (RuntimeEvent emissions)
- Per-step DB sessions for parallel execution
- Artifact cleanup on step failure
