# Execution System Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 9 verified bugs (race conditions, silent failures, missing dispatch) in the plan execution pipeline.

**Architecture:** Surgical fixes to existing functions — no new files, no migrations, no architectural changes. Each task modifies 1-2 existing functions and adds corresponding tests.

**Tech Stack:** Python 3.12, SQLAlchemy (async), pytest, pytest-asyncio, ruff

**Spec:** `docs/superpowers/specs/2026-04-13-execution-hardening-design.md`

---

## File Map

| File | Changes | Task |
|------|---------|------|
| `backend/src/services/scheduler.py` | `_tick_dlq_retry()` dispatch + `_tick_background_tasks()` row lock | 1, 2 |
| `backend/src/api/routes_ws.py` | `_handle_approve()` + `_handle_reject()` key fix | 3 |
| `backend/src/services/graph_executor.py` | `_resolve_step_references()` logging + `_writeback_memories()` level + `_execute_dag()` timer | 4, 7, 8 |
| `backend/src/services/risk_assessor.py` | `get_or_create_trust_state()` row lock | 5 |
| `docs/architecture/execution.md` | Correct parallel claim | 6 |
| `docs/architecture/plan-execution-deep-dive.md` | Correct parallel claim | 6 |
| `backend/src/orchestrator/jarvis.py` | `_handle_system_capability()` InteractionLog | 9 |
| `backend/tests/test_dlq_retry_dispatch.py` | New test file | 1 |
| `backend/tests/test_background_task_locking.py` | New test file | 2 |
| `backend/tests/test_ws_approval_key.py` | New test file | 3 |
| `backend/tests/test_step_reference_logging.py` | New test file | 4 |
| `backend/tests/test_trust_locking.py` | New test file | 5 |

---

## Task 1: DLQ Retry Dispatch [CRITICAL]

**Files:**
- Modify: `backend/src/services/scheduler.py:511-534`
- Create: `backend/tests/test_dlq_retry_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_dlq_retry_dispatch.py
"""Tests for DLQ retry dispatch — verifies that _tick_dlq_retry actually re-executes operations."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_dlq_entry(**overrides):
    """Factory for mock DeadLetterEntry objects."""
    defaults = dict(
        entry_id="dlq_test_001",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        operation_type="background_task",
        source_id="run_test_001",
        error_type="TimeoutError",
        error_message="Connection timed out",
        attempt_count=1,
        max_attempts=3,
        status="pending",
        payload={"run_id": "run_test_001", "plan_id": "plan_test_001"},
    )
    defaults.update(overrides)
    entry = MagicMock()
    for k, v in defaults.items():
        setattr(entry, k, v)
    return entry


@pytest.mark.asyncio
async def test_dlq_retry_dispatches_background_task():
    """After mark_retrying, background_task handler transitions run to pending."""
    settings = make_mock_settings()
    scheduler = _make_scheduler(settings)

    entry = _make_dlq_entry(operation_type="background_task")
    mock_dlq = AsyncMock()
    mock_dlq.list_pending.return_value = [entry]
    mock_dlq.mark_retrying.return_value = True
    mock_dlq.mark_resolved = AsyncMock()

    mock_run = MagicMock(status="failed")

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=mock_run)
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()

    factory = AsyncMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq):
        with patch("src.services.execution_state.transition_run") as mock_transition:
            await scheduler._tick_dlq_retry(factory)

    mock_dlq.mark_retrying.assert_called_once_with("dlq_test_001")
    mock_transition.assert_called_once_with(mock_run, "pending")
    mock_dlq.mark_resolved.assert_called_once_with("dlq_test_001")


@pytest.mark.asyncio
async def test_dlq_retry_unknown_operation_type_logs_warning():
    """Unknown operation_type logs warning but doesn't crash."""
    settings = make_mock_settings()
    scheduler = _make_scheduler(settings)

    entry = _make_dlq_entry(operation_type="unknown_op")
    mock_dlq = AsyncMock()
    mock_dlq.list_pending.return_value = [entry]
    mock_dlq.mark_retrying.return_value = True

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()

    factory = AsyncMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq):
        with patch("src.services.scheduler.logger") as mock_logger:
            await scheduler._tick_dlq_retry(factory)

    mock_logger.warning.assert_called()
    assert "unknown_op" in str(mock_logger.warning.call_args)


@pytest.mark.asyncio
async def test_dlq_retry_handler_failure_does_not_crash():
    """If the retry handler raises, the entry stays retrying and loop continues."""
    settings = make_mock_settings()
    scheduler = _make_scheduler(settings)

    entry = _make_dlq_entry(operation_type="background_task")
    mock_dlq = AsyncMock()
    mock_dlq.list_pending.return_value = [entry]
    mock_dlq.mark_retrying.return_value = True

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(side_effect=RuntimeError("DB unavailable"))
    mock_db.commit = AsyncMock()

    factory = AsyncMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq):
        # Should not raise
        await scheduler._tick_dlq_retry(factory)

    mock_dlq.mark_resolved.assert_not_called()


def _make_scheduler(settings):
    """Create a SchedulerLoop with minimal mocks for DLQ testing."""
    from src.services.scheduler import SchedulerLoop

    scheduler = SchedulerLoop.__new__(SchedulerLoop)
    scheduler._settings = settings
    scheduler._user_ids = [TEST_USER_ID]
    scheduler._orchestrator = MagicMock()
    scheduler._running = True
    return scheduler
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_dlq_retry_dispatch.py -v`
Expected: FAIL — `_tick_dlq_retry` doesn't call `transition_run` or `mark_resolved`

- [ ] **Step 3: Implement the DLQ dispatch logic**

Replace `_tick_dlq_retry` in `backend/src/services/scheduler.py` (lines 511-534):

```python
async def _tick_dlq_retry(self, factory) -> None:
    """Retry DLQ entries that haven't exceeded max attempts."""
    try:
        async with factory() as db:
            from src.services.dead_letter import DeadLetterService
            from src.services.execution_state import transition_run

            dlq = DeadLetterService(db)
            for uid in self._user_ids:
                pending = await dlq.list_pending(uid, limit=10)
                for entry in pending:
                    if not await dlq.mark_retrying(entry.entry_id):
                        logger.info(
                            "DLQ entry %s exhausted, marked as exhausted",
                            entry.entry_id,
                        )
                        continue

                    resolved = await self._dispatch_dlq_entry(
                        db, entry, factory
                    )
                    if resolved:
                        await dlq.mark_resolved(entry.entry_id)
                    else:
                        logger.warning(
                            "DLQ retry failed for entry %s (op=%s)",
                            entry.entry_id,
                            entry.operation_type,
                        )
                await db.commit()
    except Exception:
        logger.warning("DLQ retry tick failed", exc_info=True)

async def _dispatch_dlq_entry(
    self, db, entry, factory
) -> bool:
    """Dispatch a DLQ entry to the appropriate retry handler.

    Returns True if the retry succeeded and the entry can be resolved.
    """
    try:
        op = entry.operation_type
        payload = entry.payload or {}

        if op == "background_task":
            # Re-queue the TaskRun as pending — next _tick_background_tasks picks it up
            from src.models.task_graph import TaskRun
            from src.services.execution_state import transition_run

            run_id = payload.get("run_id")
            if run_id:
                run = await db.get(TaskRun, run_id)
                if run and run.status == "failed":
                    transition_run(run, "pending")
                    await db.flush()
                    return True
            logger.warning("DLQ background_task missing run_id or run not found")
            return False

        elif op == "failed_embedding":
            from src.services.embedding import EmbeddingService

            svc = EmbeddingService(self._settings)
            text = payload.get("text", "")
            collection = payload.get("collection", "memories")
            point_id = payload.get("point_id")
            if text and point_id:
                vector = await svc.embed_text(text)
                if vector:
                    return True
            return False

        elif op == "perception_cycle":
            # Re-enable the perception source for next tick
            source = payload.get("source")
            if source and self._orchestrator:
                await self._orchestrator._bump_perception_for_sources([source])
                return True
            return False

        else:
            logger.warning(
                "No DLQ handler for operation_type=%s (entry=%s)",
                op,
                entry.entry_id,
            )
            return False

    except Exception:
        logger.warning(
            "DLQ dispatch failed for entry %s", entry.entry_id, exc_info=True
        )
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_dlq_retry_dispatch.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Lint**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && ruff check src/services/scheduler.py tests/test_dlq_retry_dispatch.py && ruff format src/services/scheduler.py tests/test_dlq_retry_dispatch.py`

- [ ] **Step 6: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/services/scheduler.py tests/test_dlq_retry_dispatch.py
git commit -m "fix: implement DLQ retry dispatch — actually re-execute dead-lettered operations"
```

---

## Task 2: Background Task Row Locking [CRITICAL]

**Files:**
- Modify: `backend/src/services/scheduler.py:344-352`
- Create: `backend/tests/test_background_task_locking.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_background_task_locking.py
"""Tests for background task pickup row locking."""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from sqlalchemy import select

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


@pytest.mark.asyncio
async def test_background_task_query_uses_for_update_skip_locked():
    """The SELECT query must include .with_for_update(skip_locked=True)."""
    settings = make_mock_settings()

    # We'll inspect the compiled query to verify FOR UPDATE SKIP LOCKED
    captured_queries = []
    original_execute = None

    async def capturing_execute(stmt, *args, **kwargs):
        # Capture the compiled SQL string
        captured_queries.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        # Return empty result
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    mock_db = AsyncMock()
    mock_db.execute = capturing_execute

    factory = AsyncMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    from src.services.scheduler import SchedulerLoop

    scheduler = SchedulerLoop.__new__(SchedulerLoop)
    scheduler._settings = settings
    scheduler._user_ids = [TEST_USER_ID]
    scheduler._orchestrator = MagicMock()
    scheduler._running = True

    await scheduler._tick_background_tasks(factory)

    assert len(captured_queries) >= 1, "Expected at least one query to be executed"
    query_str = captured_queries[0].lower()
    assert "for update" in query_str, f"Query missing FOR UPDATE: {captured_queries[0]}"
    assert "skip locked" in query_str, f"Query missing SKIP LOCKED: {captured_queries[0]}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_background_task_locking.py -v`
Expected: FAIL — query does not contain "FOR UPDATE"

- [ ] **Step 3: Add row locking to the query**

In `backend/src/services/scheduler.py`, modify the query at lines 344-352. Add `.with_for_update(skip_locked=True)` before the closing parenthesis:

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

This is a single-line addition. No other changes required.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_background_task_locking.py -v`
Expected: PASS

- [ ] **Step 5: Run existing scheduler tests for regression**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_scheduler.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/services/scheduler.py tests/test_background_task_locking.py
git commit -m "fix: add FOR UPDATE SKIP LOCKED to background task pickup query"
```

---

## Task 3: WebSocket Approval Payload Key [CRITICAL]

**Files:**
- Modify: `backend/src/api/routes_ws.py:206-213`
- Create: `backend/tests/test_ws_approval_key.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ws_approval_key.py
"""Tests for WebSocket approval payload key resolution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_handle_approve_uses_approval_id_key():
    """_handle_approve should extract approval_id from 'approval_id' key."""
    from src.api.routes_ws import _handle_approve

    mock_app = MagicMock()
    payload = {"approval_id": "apr_test_001"}

    with patch("src.api.routes_ws._process_approval_ws", new_callable=AsyncMock) as mock_process:
        mock_process.return_value = {"status": "success"}
        result = await _handle_approve("usr_test", payload, mock_app)

    mock_process.assert_called_once_with("usr_test", "apr_test_001", "approve", mock_app)


@pytest.mark.asyncio
async def test_handle_approve_falls_back_to_id_key():
    """_handle_approve should fall back to 'id' key for backwards compatibility."""
    from src.api.routes_ws import _handle_approve

    mock_app = MagicMock()
    payload = {"id": "apr_test_002"}

    with patch("src.api.routes_ws._process_approval_ws", new_callable=AsyncMock) as mock_process:
        mock_process.return_value = {"status": "success"}
        result = await _handle_approve("usr_test", payload, mock_app)

    mock_process.assert_called_once_with("usr_test", "apr_test_002", "approve", mock_app)


@pytest.mark.asyncio
async def test_handle_reject_uses_approval_id_key():
    """_handle_reject should extract approval_id from 'approval_id' key."""
    from src.api.routes_ws import _handle_reject

    mock_app = MagicMock()
    payload = {"approval_id": "apr_test_003"}

    with patch("src.api.routes_ws._process_approval_ws", new_callable=AsyncMock) as mock_process:
        mock_process.return_value = {"status": "success"}
        result = await _handle_reject("usr_test", payload, mock_app)

    mock_process.assert_called_once_with("usr_test", "apr_test_003", "reject", mock_app)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_ws_approval_key.py -v`
Expected: FAIL — `_process_approval_ws` called with empty string instead of `"apr_test_001"`

- [ ] **Step 3: Fix the key extraction**

In `backend/src/api/routes_ws.py`, replace lines 206-213:

```python
async def _handle_approve(user_id: str, payload: dict, app) -> dict:
    """Handle approval action via the REST handler (full execution resume)."""
    approval_id = payload.get("approval_id") or payload.get("id", "")
    return await _process_approval_ws(user_id, approval_id, "approve", app)


async def _handle_reject(user_id: str, payload: dict, app) -> dict:
    """Handle rejection action via the REST handler (full execution resume)."""
    approval_id = payload.get("approval_id") or payload.get("id", "")
    return await _process_approval_ws(user_id, approval_id, "reject", app)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_ws_approval_key.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/api/routes_ws.py tests/test_ws_approval_key.py
git commit -m "fix: use 'approval_id' key in WebSocket approval handlers with 'id' fallback"
```

---

## Task 4: Step Reference Resolution Logging [HIGH]

**Files:**
- Modify: `backend/src/services/graph_executor.py:1434-1452`
- Create: `backend/tests/test_step_reference_logging.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_step_reference_logging.py
"""Tests for step reference resolution logging on failure."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_executor():
    """Create a minimal GraphExecutor for reference resolution testing."""
    from src.services.graph_executor import GraphExecutor

    executor = GraphExecutor.__new__(GraphExecutor)
    executor._db = AsyncMock()
    return executor


def _make_step(step_id="step_01", task_id="task_01", input_data=None):
    """Factory for mock TaskStep."""
    step = MagicMock()
    step.step_id = step_id
    step.task_id = task_id
    step.input_data = input_data
    return step


def _make_completed_step(step_id, task_id, output_data):
    """Factory for mock completed TaskStep with output."""
    step = MagicMock()
    step.step_id = step_id
    step.task_id = task_id
    step.output_data = output_data
    step.status = "completed"
    step.created_at = None
    return step


@pytest.mark.asyncio
async def test_unresolved_task_reference_logs_warning(caplog):
    """When {task_id}.output.field references a missing task, log a warning."""
    executor = _make_executor()

    # No completed steps — reference can't resolve
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    executor._db.execute = AsyncMock(return_value=mock_result)

    step = _make_step(input_data={"account": "{missing_task}.output.account_id"})

    with caplog.at_level(logging.WARNING, logger="src.services.graph_executor"):
        result = await executor._resolve_step_references(step, "run_01")

    assert result["account"] == "{missing_task}.output.account_id"
    assert any("missing_task" in r.message and "not found" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_missing_field_in_output_logs_warning(caplog):
    """When field doesn't exist in upstream output, log a warning."""
    executor = _make_executor()

    upstream = _make_completed_step("step_00", "task_00", {"name": "Alice"})
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [upstream]
    executor._db.execute = AsyncMock(return_value=mock_result)

    step = _make_step(input_data={"email": "{task_00}.output.email"})

    with caplog.at_level(logging.WARNING, logger="src.services.graph_executor"):
        result = await executor._resolve_step_references(step, "run_01")

    assert result["email"] == "{task_00}.output.email"
    assert any("email" in r.message and "not in" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_successful_resolution_no_warnings(caplog):
    """Successful reference resolution should not log warnings."""
    executor = _make_executor()

    upstream = _make_completed_step("step_00", "task_00", {"account_id": "12345"})
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [upstream]
    executor._db.execute = AsyncMock(return_value=mock_result)

    step = _make_step(input_data={"account": "{task_00}.output.account_id"})

    with caplog.at_level(logging.WARNING, logger="src.services.graph_executor"):
        result = await executor._resolve_step_references(step, "run_01")

    assert result["account"] == "12345"
    assert not any("not found" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_step_reference_logging.py -v`
Expected: First 2 tests FAIL — no warning logs emitted

- [ ] **Step 3: Add logging to _resolve_step_references**

Replace the method in `backend/src/services/graph_executor.py` (lines 1434-1452):

```python
    async def _resolve_step_references(self, step: TaskStep, run_id: str) -> dict:
        """Resolve {task_id}.output.field references in step input_data.

        Enables declarative wiring between DAG steps: a downstream step
        can reference an upstream step's output by task_id.
        """
        input_data = dict(step.input_data or {})
        all_steps = await self._get_all_steps(run_id)
        outputs_by_task = {s.task_id: s.output_data for s in all_steps if s.output_data}

        def resolve(value):
            if isinstance(value, str) and value.startswith("{") and "}.output." in value:
                ref, _, field = value[1:].partition("}.output.")
                source = outputs_by_task.get(ref)
                if source is None:
                    logger.warning(
                        "Step reference unresolved: task '%s' not found in completed "
                        "steps (run_id=%s, step=%s)",
                        ref,
                        run_id,
                        step.step_id,
                    )
                    return value
                if isinstance(source, dict) and field not in source:
                    logger.warning(
                        "Step reference field missing: '%s' not in task '%s' output "
                        "(run_id=%s, step=%s, available_keys=%s)",
                        field,
                        ref,
                        run_id,
                        step.step_id,
                        list(source.keys()),
                    )
                    return value
                if isinstance(source, dict):
                    return source.get(field, value)
            return value

        resolved = {k: resolve(v) for k, v in input_data.items()}
        unresolved = [
            k for k, v in resolved.items() if isinstance(v, str) and "}.output." in v
        ]
        if unresolved:
            logger.warning(
                "Step %s has %d unresolved reference(s): %s",
                step.step_id,
                len(unresolved),
                unresolved,
            )
        return resolved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_step_reference_logging.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Run existing graph executor tests for regression**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_graph_executor.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/services/graph_executor.py tests/test_step_reference_logging.py
git commit -m "fix: log warnings when step reference resolution fails"
```

---

## Task 5: Trust Graduation Pessimistic Locking [HIGH]

**Files:**
- Modify: `backend/src/services/risk_assessor.py:255-283`
- Create: `backend/tests/test_trust_locking.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_trust_locking.py
"""Tests for trust graduation pessimistic locking."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_get_or_create_trust_state_uses_for_update():
    """get_or_create_trust_state must lock the row with FOR UPDATE."""
    captured_queries = []

    async def capturing_execute(stmt, *args, **kwargs):
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        captured_queries.append(compiled)
        result = MagicMock()
        mock_state = MagicMock(
            workspace_id="ws_test",
            capability="email.send",
            risk_level="medium",
            approved_count=3,
            rejected_count=0,
            modified_count=0,
            trust_level="learning",
        )
        result.scalar_one_or_none.return_value = mock_state
        return result

    mock_db = AsyncMock()
    mock_db.execute = capturing_execute

    from src.services.risk_assessor import get_or_create_trust_state

    await get_or_create_trust_state(mock_db, "ws_test", "email.send", "medium")

    assert len(captured_queries) >= 1, "Expected at least one query"
    query_str = captured_queries[0].lower()
    assert "for update" in query_str, f"Query missing FOR UPDATE: {captured_queries[0]}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_trust_locking.py -v`
Expected: FAIL — query does not contain "FOR UPDATE"

- [ ] **Step 3: Add pessimistic locking**

In `backend/src/services/risk_assessor.py`, modify `get_or_create_trust_state()` (lines 255-283). Add `.with_for_update()` to the SELECT:

```python
async def get_or_create_trust_state(
    db: AsyncSession, workspace_id: str, capability: str, risk_level: str
):
    """Get existing TrustState or create a new one.

    Uses SELECT ... FOR UPDATE to serialize concurrent writes to the
    same (workspace_id, capability, risk_level) tuple.
    """
    from src.models.trust_state import TrustState

    result = await db.execute(
        select(TrustState)
        .where(
            TrustState.workspace_id == workspace_id,
            TrustState.capability == capability,
            TrustState.risk_level == risk_level,
        )
        .with_for_update()
    )
    state = result.scalar_one_or_none()
    if state:
        return state

    state = TrustState(
        workspace_id=workspace_id,
        capability=capability,
        risk_level=risk_level,
        approved_count=0,
        rejected_count=0,
        modified_count=0,
        trust_level="first_use",
    )
    db.add(state)
    await db.flush()
    return state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_trust_locking.py -v`
Expected: PASS

- [ ] **Step 5: Run existing trust tests for regression**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_trust_engine_v2.py tests/test_risk_assessor.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/services/risk_assessor.py tests/test_trust_locking.py
git commit -m "fix: add FOR UPDATE lock to trust state queries — prevent race in graduation"
```

---

## Task 6: Sequential Execution Documentation Fix [HIGH]

**Files:**
- Modify: `docs/architecture/execution.md:142`
- Modify: `docs/architecture/plan-execution-deep-dive.md` (DAG Resolution section)

- [ ] **Step 1: Fix execution.md**

In `docs/architecture/execution.md`, replace line 142:

Old:
```
4. **Parallel execution** - Independent steps run concurrently via `asyncio.gather()`
```

New:
```
4. **Sequential execution** - Ready steps are executed sequentially within each batch (SQLAlchemy AsyncSession is not safe for concurrent coroutines sharing one session). Future: per-step sessions for parallelism.
```

Also update lines 149-151, changing the example comment:

Old:
```
Execution order: [A] -> [B, C] (parallel) -> [D]
```

New:
```
Execution order: [A] -> [B, C] (sequential within batch) -> [D]
```

- [ ] **Step 2: Fix plan-execution-deep-dive.md**

In `docs/architecture/plan-execution-deep-dive.md`, find and update the DAG Resolution Algorithm section. The description should state steps execute sequentially, not via `asyncio.gather()`. Update any mention of "parallel" in the step execution context to "sequential within batch."

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis
git add docs/architecture/execution.md docs/architecture/plan-execution-deep-dive.md
git commit -m "docs: correct parallel execution claim — steps run sequentially per batch"
```

---

## Task 7: Memory Writeback Logging Level [MEDIUM]

**Files:**
- Modify: `backend/src/services/graph_executor.py:1508-1509`

- [ ] **Step 1: Fix the logging level**

In `backend/src/services/graph_executor.py`, replace lines 1508-1509:

Old:
```python
        except Exception:
            logger.debug("Memory writeback failed", exc_info=True)
```

New:
```python
        except Exception:
            logger.warning(
                "Memory writeback failed for run %s — execution memories not stored",
                run.run_id,
                exc_info=True,
            )
```

- [ ] **Step 2: Run existing tests for regression**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_graph_executor.py -v`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/services/graph_executor.py
git commit -m "fix: escalate memory writeback failure from debug to warning with run_id"
```

---

## Task 8: Long DAG Session Warning [MEDIUM]

**Files:**
- Modify: `backend/src/services/graph_executor.py:553-559`

- [ ] **Step 1: Add elapsed time tracking**

In `backend/src/services/graph_executor.py`, add timing at the start and end of `_execute_dag()`. After line 559 (`"""Main DAG execution loop."""`), add a timer start. At the end of the method (before the final return or after the while loop exits), add the warning check:

At the start of `_execute_dag` (after the docstring, before the `while True:` line):

```python
        _dag_start = time.monotonic()
```

At the end of the method, just before it returns (after the while loop completes — find the final completion/failure block):

```python
        _dag_elapsed = time.monotonic() - _dag_start
        if _dag_elapsed > 120:
            logger.warning(
                "Long DAG execution: run %s took %.1fs — "
                "consider db_factory pattern for connection pool safety",
                run.run_id,
                _dag_elapsed,
            )
```

Also add `import time` at the top of the file if not already present.

- [ ] **Step 2: Run existing tests for regression**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_graph_executor.py -v`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/services/graph_executor.py
git commit -m "fix: log warning when DAG execution exceeds 120s"
```

---

## Task 9: System Capability InteractionLog Audit [MEDIUM]

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:2942-2960`

- [ ] **Step 1: Add InteractionLog alongside existing PlanTask audit**

In `backend/src/orchestrator/jarvis.py`, after the existing PlanTask audit block (around line 2958), add an InteractionLog entry. The existing code at lines 2942-2960 creates a PlanTask. Add the InteractionLog below it, inside the same try block:

```python
        # Audit: record as completed PlanTask
        if plan.plan_id:
            try:
                from src.models.plans import PlanTask
                from src.models.interaction_log import InteractionLog

                async with self._db_factory() as db:
                    db.add(
                        PlanTask(
                            task_id=f"ptask_{ULID()}",
                            plan_id=plan.plan_id,
                            workspace_id=workspace_id,
                            task_type=cap,
                            input_data=step.input or {"description": step.description},
                            status="completed",
                        )
                    )
                    db.add(
                        InteractionLog(
                            interaction_id=f"ilog_{ULID()}",
                            user_id=user_id,
                            workspace_id=workspace_id,
                            interaction_type=cap,
                            user_message=step.description[:500],
                            assistant_response=str(result)[:500] if result else "completed",
                            metadata_={"plan_step": step.step_id, "actor": "system"},
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to audit system capability step", exc_info=True)
```

- [ ] **Step 2: Verify InteractionLog model exists and has required fields**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -c "from src.models.interaction_log import InteractionLog; print('OK:', [c.name for c in InteractionLog.__table__.columns][:8])"`
Expected: prints column names including `interaction_id`, `user_id`, `workspace_id`, `interaction_type`

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/ -v -x --timeout=60 -q`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
git add src/orchestrator/jarvis.py
git commit -m "fix: add InteractionLog audit record for system capability steps"
```

---

## Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
python -m pytest tests/ -v --timeout=60
```

- [ ] **Step 2: Lint and format**

```bash
cd /Users/sivasankarreddybogala/work/jarvis/backend
ruff check src/ tests/
ruff format src/ tests/
```

- [ ] **Step 3: Verify all commits**

```bash
git log --oneline -10
```

Expected: 9 commits, each with a descriptive message.

---

## Update Spec with Verification Results

After implementation, update `docs/superpowers/specs/2026-04-13-execution-hardening-design.md` to note which fixes were already implemented on the branch and which were newly implemented. Commit the update.
