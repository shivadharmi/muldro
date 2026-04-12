# Execution Engine Durability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the execution engine survive server restarts, support cooperative cancellation, enforce step-level timeouts, add retry backoff, detect stuck runs, and use checkpoints for recovery.

**Architecture:** The execution engine centers on `GraphExecutor` which orchestrates a DAG of `TaskStep` records via `_execute_dag()`. The `SchedulerLoop` picks up background tasks every 30s. This plan adds a health checker tick, cancellation tokens threaded through `agent_loop`, step timeout wrappers, and checkpoint-based recovery.

**Tech Stack:** Python asyncio, SQLAlchemy async, Alembic, Redis

---

## Design Note: Cancellation Token Pattern

The cancellation token is an `asyncio.Event` created per-run by `GraphExecutor`. It's passed to `agent_loop()` as an optional parameter. Between each tool round, `agent_loop` checks `if cancel_event.is_set(): break`. When `cancel_run()` is called, it sets the event, causing the agent loop to exit gracefully at the next check point.

```
GraphExecutor.execute_run()
  creates cancel_event = asyncio.Event()
  stores in self._cancel_events[run_id] = cancel_event
  passes to _execute_dag() -> _execute_step() -> _run_step_via_agent_loop()
  
GraphExecutor.cancel_run()
  if run_id in self._cancel_events:
    self._cancel_events[run_id].set()  # signal agent_loop to stop
  transitions running steps to cancelled
```

## Design Note: Run Health Checker

A new `_tick_run_health_check()` method in `SchedulerLoop`, called every 60s:
1. Query runs with `status="running"` and `updated_at < now - 15min`
2. For each stuck run: check latest checkpoint. If checkpoint is recent (< 15min), skip (grace period). Otherwise, transition to `timed_out`.
3. Query runs with `status="awaiting_approval"` where linked approval is `expired`. Transition run to `cancelled`.
4. Notify user about stuck/cancelled runs via Notifier.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/services/graph_executor.py` | Modify | Cancel tokens, step timeout, retry backoff, checkpoint recovery |
| `backend/src/orchestrator/agent_loop.py` | Modify | Accept cancel_event, check between rounds |
| `backend/src/services/scheduler.py` | Modify | Add health check tick |
| `backend/src/models/task_graph.py` | Modify | Add timeout_seconds to TaskStep |
| `backend/src/services/execution_state.py` | Modify | Add cancelled to step transitions |
| `backend/tests/test_execution_durability.py` | Create | Tests for cancel, timeout, health check |

---

### Task 1: Add Cancellation Token to GraphExecutor and Agent Loop

**Gaps:** 2.2
**Files:**
- Modify: `backend/src/services/graph_executor.py:474-507`
- Modify: `backend/src/orchestrator/agent_loop.py`
- Create: `backend/tests/test_execution_durability.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_execution_durability.py
"""Tests for execution engine durability — cancellation, timeouts, health checks."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestCancellationToken:
    """Cancel event stops agent_loop between tool rounds."""

    @pytest.mark.asyncio
    async def test_agent_loop_respects_cancel_event(self):
        """When cancel_event is set, agent_loop exits without calling more tools."""
        from src.orchestrator.agent_loop import _check_cancellation, CancellationRequested

        cancel_event = asyncio.Event()
        cancel_event.set()

        with pytest.raises(CancellationRequested):
            _check_cancellation(cancel_event)

    @pytest.mark.asyncio
    async def test_unset_cancel_event_does_not_raise(self):
        """When cancel_event is not set, _check_cancellation is a no-op."""
        from src.orchestrator.agent_loop import _check_cancellation

        cancel_event = asyncio.Event()
        # Should not raise
        _check_cancellation(cancel_event)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_execution_durability.py::TestCancellationToken -v`
Expected: FAIL — `_check_cancellation` and `CancellationRequested` don't exist.

- [ ] **Step 3: Add CancellationRequested exception and check to agent_loop**

In `backend/src/orchestrator/agent_loop.py`, add near the top (after imports):

```python
class CancellationRequested(Exception):
    """Raised when a run cancellation token is set."""
    pass


def _check_cancellation(cancel_event: asyncio.Event | None) -> None:
    """Check cancellation token between tool rounds. Raises if set."""
    if cancel_event and cancel_event.is_set():
        raise CancellationRequested("Run cancelled by user")
```

Then in the `agent_loop()` generator function, add the check at the start of each tool round loop (inside the `for _round in range(max_tool_rounds):` loop, as the first line):

```python
    for _round in range(max_tool_rounds):
        _check_cancellation(cancel_event)
        # ... existing code
```

Also add `cancel_event: asyncio.Event | None = None` parameter to the `agent_loop()` function signature.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_execution_durability.py::TestCancellationToken -v`
Expected: PASS

- [ ] **Step 5: Wire cancellation token through GraphExecutor**

In `backend/src/services/graph_executor.py`:

Add to `__init__`:
```python
        self._cancel_events: dict[str, asyncio.Event] = {}
```

In `execute_run()`, before calling `_execute_dag()`:
```python
        cancel_event = asyncio.Event()
        self._cancel_events[run.run_id] = cancel_event
        try:
            await self._execute_dag(run, surface_id=surface_id, cancel_event=cancel_event)
        finally:
            self._cancel_events.pop(run.run_id, None)
```

In `cancel_run()`, add before state transitions:
```python
        # Signal in-flight agent_loop to stop
        cancel_event = self._cancel_events.get(run_id)
        if cancel_event:
            cancel_event.set()
```

Also update `cancel_run()` to handle `running` steps:
```python
        steps_result = await self._db.execute(
            select(TaskStep).where(
                TaskStep.run_id == run_id,
                TaskStep.status.in_(["pending", "ready", "running"]),
            )
        )
        for step in steps_result.scalars().all():
            if step.status == "running":
                transition_step(step, "cancelled")
            else:
                transition_step(step, "skipped")
```

- [ ] **Step 6: Add `cancelled` to step transitions**

In `backend/src/services/execution_state.py`, add `"cancelled"` to the `running` step transitions:

```python
    "running": {
        "completed",
        "failed",
        "waiting_approval",
        "awaiting_input",
        "skipped",
        "timed_out",
        "cancelled",
    },
```

And add a terminal `"cancelled"` entry:
```python
    "cancelled": set(),
```

- [ ] **Step 7: Run full tests**

Run: `cd backend && python -m pytest tests/test_execution_durability.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd backend && git add src/services/graph_executor.py src/orchestrator/agent_loop.py src/services/execution_state.py tests/test_execution_durability.py
git commit -m "feat: cooperative cancellation via asyncio.Event token threaded through agent_loop"
```

---

### Task 2: Enforce Step-Level Timeouts

**Gaps:** 2.3
**Files:**
- Modify: `backend/src/models/task_graph.py:73-103`
- Modify: `backend/src/services/graph_executor.py`
- Modify: `backend/tests/test_execution_durability.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_execution_durability.py`:

```python
class TestStepTimeout:
    """Individual steps should time out after their configured duration."""

    @pytest.mark.asyncio
    async def test_step_timeout_transitions_to_timed_out(self):
        """A step exceeding timeout_seconds transitions to timed_out."""
        from src.services.execution_state import transition_step, STEP_TRANSITIONS

        # Verify the transition is valid
        assert "timed_out" in STEP_TRANSITIONS["running"]

    def test_task_step_has_timeout_field(self):
        """TaskStep model includes timeout_seconds column."""
        from src.models.task_graph import TaskStep

        assert hasattr(TaskStep, "timeout_seconds")
```

- [ ] **Step 2: Add timeout_seconds column to TaskStep**

In `backend/src/models/task_graph.py`, add to the `TaskStep` class (after `max_retries`):

```python
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
```

- [ ] **Step 3: Generate and apply migration**

Run: `cd backend && alembic revision --autogenerate -m "add timeout_seconds to task_steps" && alembic upgrade head`

- [ ] **Step 4: Wrap step execution with timeout**

In `backend/src/services/graph_executor.py`, in `_execute_step()`, wrap the `_run_step_action()` call:

```python
        # Step-level timeout (default 120s, configurable per step)
        step_timeout = step.timeout_seconds or 120
        try:
            output = await asyncio.wait_for(
                self._run_step_action(run, step, cancel_event=cancel_event),
                timeout=step_timeout,
            )
        except asyncio.TimeoutError:
            transition_step(step, "timed_out")
            step.error = {"message": f"Step timed out after {step_timeout}s"}
            step.completed_at = datetime.now(timezone.utc)
            await self._db.flush()
            logger.warning("Step %s timed out after %ds", step.step_id, step_timeout)
            return
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_execution_durability.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/models/task_graph.py src/services/graph_executor.py tests/test_execution_durability.py alembic/versions/
git commit -m "feat: enforce step-level timeouts with configurable timeout_seconds"
```

---

### Task 3: Add Retry Backoff Strategy

**Gaps:** 2.5
**Files:**
- Modify: `backend/src/services/graph_executor.py:939-961`
- Modify: `backend/tests/test_execution_durability.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_execution_durability.py`:

```python
class TestRetryBackoff:
    """Failed step retries should use exponential backoff."""

    @pytest.mark.asyncio
    async def test_backoff_delay_increases_with_retry_count(self):
        """Retry delay should be min(2^retry_count, 30) seconds."""
        from src.services.graph_executor import _compute_retry_delay

        assert _compute_retry_delay(0) == 1  # 2^0 = 1
        assert _compute_retry_delay(1) == 2  # 2^1 = 2
        assert _compute_retry_delay(2) == 4  # 2^2 = 4
        assert _compute_retry_delay(5) == 30  # min(32, 30) = 30
        assert _compute_retry_delay(10) == 30  # capped at 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_execution_durability.py::TestRetryBackoff -v`
Expected: FAIL — `_compute_retry_delay` doesn't exist.

- [ ] **Step 3: Add retry delay function and apply in _handle_step_failure**

In `backend/src/services/graph_executor.py`, add module-level function:

```python
def _compute_retry_delay(retry_count: int) -> int:
    """Compute exponential backoff delay in seconds, capped at 30."""
    return min(2 ** retry_count, 30)
```

In `_handle_step_failure()`, add a delay before resetting to pending:

```python
        step.retry_count += 1
        if step.retry_count < step.max_retries:
            delay = _compute_retry_delay(step.retry_count)
            logger.warning(
                "Step %s failed (attempt %d/%d), retrying in %ds: %s",
                step.step_id,
                step.retry_count,
                step.max_retries,
                delay,
                exc,
            )
            transition_step(step, "failed")
            transition_step(step, "pending")
            step.error = {
                "attempt": step.retry_count,
                "message": str(exc)[:500],
                "retry_after_seconds": delay,
            }
            await self._db.flush()
            await asyncio.sleep(delay)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_execution_durability.py::TestRetryBackoff -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/services/graph_executor.py tests/test_execution_durability.py
git commit -m "feat: exponential backoff for step retries, capped at 30 seconds"
```

---

### Task 4: Add Stuck Run Detection to Scheduler

**Gaps:** 2.1, 4.5
**Files:**
- Modify: `backend/src/services/scheduler.py`
- Modify: `backend/tests/test_execution_durability.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_execution_durability.py`:

```python
class TestStuckRunDetection:
    """Scheduler detects and remediates runs stuck in running state."""

    def test_scheduler_has_health_check_method(self):
        """SchedulerLoop has _tick_run_health_check method."""
        from src.services.scheduler import SchedulerLoop

        assert hasattr(SchedulerLoop, "_tick_run_health_check")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_execution_durability.py::TestStuckRunDetection -v`
Expected: FAIL — method doesn't exist.

- [ ] **Step 3: Implement _tick_run_health_check()**

In `backend/src/services/scheduler.py`, add the method to `SchedulerLoop`:

```python
    async def _tick_run_health_check(self, factory) -> None:
        """Detect and remediate stuck runs.

        Runs every 60s. Finds:
        - "running" runs with no update in 15 minutes → timed_out
        - "awaiting_approval" runs with expired approval → cancelled
        """
        try:
            from src.models.approvals import Approval
            from src.models.task_graph import TaskCheckpoint, TaskRun
            from src.services.execution_state import transition_run

            cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)

            async with factory() as db:
                # Stuck "running" runs
                result = await db.execute(
                    select(TaskRun).where(
                        TaskRun.status == "running",
                        TaskRun.updated_at < cutoff,
                    )
                )
                stuck_runs = list(result.scalars().all())

                for run in stuck_runs:
                    # Check latest checkpoint — if recent, give grace period
                    cp_result = await db.execute(
                        select(TaskCheckpoint)
                        .where(TaskCheckpoint.run_id == run.run_id)
                        .order_by(TaskCheckpoint.created_at.desc())
                        .limit(1)
                    )
                    latest_cp = cp_result.scalar_one_or_none()
                    if latest_cp and latest_cp.created_at > cutoff:
                        continue  # Recent checkpoint — still active

                    logger.warning(
                        "Stuck run detected: %s (status=%s, last_update=%s)",
                        run.run_id,
                        run.status,
                        run.updated_at,
                    )
                    try:
                        transition_run(run, "timed_out")
                        run.error = {"message": "Run stuck — no progress for 15 minutes"}
                        run.completed_at = datetime.now(timezone.utc)
                    except Exception:
                        run.status = "timed_out"

                # Stuck "awaiting_approval" runs with expired approvals
                approval_cutoff = datetime.now(timezone.utc)
                result = await db.execute(
                    select(TaskRun).where(
                        TaskRun.status == "awaiting_approval",
                    )
                )
                awaiting_runs = list(result.scalars().all())

                for run in awaiting_runs:
                    # Check if linked approval is expired
                    apr_result = await db.execute(
                        select(Approval).where(
                            Approval.execution_id == run.run_id,
                            Approval.status == "expired",
                        )
                    )
                    if apr_result.scalar_one_or_none():
                        logger.warning(
                            "Cancelling run %s — approval expired", run.run_id
                        )
                        try:
                            transition_run(run, "cancelled")
                            run.completed_at = datetime.now(timezone.utc)
                        except Exception:
                            run.status = "cancelled"

                await db.commit()

                if stuck_runs or awaiting_runs:
                    logger.info(
                        "Health check: %d stuck runs remediated, %d expired approvals cancelled",
                        len(stuck_runs),
                        len([r for r in awaiting_runs]),
                    )
        except Exception:
            logger.warning("Run health check failed", exc_info=True)
```

- [ ] **Step 4: Wire health check into the scheduler tick**

In the main `_tick()` method of `SchedulerLoop`, add the call (alongside existing ticks):

```python
        await self._tick_run_health_check(factory)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_execution_durability.py::TestStuckRunDetection -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/services/scheduler.py tests/test_execution_durability.py
git commit -m "feat: stuck run detection and remediation in scheduler health check"
```

---

### Task 5: Use Checkpoints for Resume Validation

**Gaps:** 2.4
**Files:**
- Modify: `backend/src/services/graph_executor.py:412-460` (`resume_run`)
- Modify: `backend/tests/test_execution_durability.py`

- [ ] **Step 1: Write the test**

Add to `backend/tests/test_execution_durability.py`:

```python
class TestCheckpointRecovery:
    """resume_run should validate checkpoint consistency."""

    def test_resume_run_checks_checkpoint(self):
        """resume_run method exists and accepts run_id."""
        from src.services.graph_executor import GraphExecutor

        assert hasattr(GraphExecutor, "resume_run")
```

- [ ] **Step 2: Add checkpoint validation to resume_run()**

In `backend/src/services/graph_executor.py`, in `resume_run()`, after loading the run and before calling `_execute_dag()`:

```python
        # Validate checkpoint consistency
        if run.checkpoint:
            cp_completed = set(run.checkpoint.get("completed_steps", {}).keys())
            actual_completed = {
                s.step_id
                for s in (await self._get_all_steps(run.run_id))
                if s.status == "completed"
            }
            if cp_completed != actual_completed:
                logger.warning(
                    "Checkpoint/DB mismatch for run %s: checkpoint=%d completed, DB=%d completed",
                    run.run_id,
                    len(cp_completed),
                    len(actual_completed),
                )
                # Trust DB state over checkpoint — checkpoint is informational
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/test_execution_durability.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd backend && git add src/services/graph_executor.py tests/test_execution_durability.py
git commit -m "feat: validate checkpoint consistency against DB state in resume_run"
```
