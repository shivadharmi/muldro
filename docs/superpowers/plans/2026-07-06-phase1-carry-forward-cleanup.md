# Phase 1 — Carry-Forward Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the five safe, standalone carry-forwards from Steps 3–5 of the first-principles rebuild — two session-poisoning robustness bugs (CF-1, CF-2), one module-hygiene move (CF-3), one coverage gap (CF-4), and one dead-code removal (CF-5) — without touching any Step-6 runtime surface.

**Architecture:** Additive/surgical only. No schema changes, no migrations, no new modules of substance (CF-3 relocates one helper into an existing shared module). Two bugs share the same root cause the Step-4 review already fixed elsewhere: a best-effort DB write whose failure aborts the shared `AsyncSession`, so a *later* `flush()`/`commit()` raises `PendingRollbackError` and a healthy run is falsely reported failed / a whole batch is silently lost. The fix idiom is the repo's single existing `begin_nested()` SAVEPOINT reference (CF-1) and an explicit `rollback()` + re-hydrate (CF-2).

**Tech Stack:** Python 3.12, async SQLAlchemy (asyncpg), pytest via the repo's custom `pytest_pyfunc_call` asyncio hook (NO pytest-asyncio, NO `asyncio_mode`, NO `db_session` fixture). Real-DB tests are self-contained via the `_db_reachable` / `_run_env` / `NullPool` pattern.

---

## Infra note (verify at start)

Run all commands from `backend/` via `uv run`:

```bash
docker compose up -d postgres redis qdrant   # from repo root
cd backend
uv run alembic upgrade head                    # must report head c7d3e4f5a6b8
uv run alembic check                           # must be drift-free (no schema changes in this plan)
uv run pytest tests/ --ignore=tests/e2e        # baseline: 3105 passed / 18 skipped
```

- **NO pip.** Use `uv run …` / `uv add …`. Plain `uv sync` drops dev extras — use `uv sync --all-extras` if syncing.
- Real-DB tests self-skip via `pytestmark = pytest.mark.skipif(not _db_reachable(), ...)` when Postgres is down — they only truly exercise the fix against a live Postgres, so **verify Postgres is up** or the new tests are no-ops.
- Do **not** edit `backend/` files while a `uvicorn --reload` worker is running.
- **No migrations in this plan** → no live-DB up/down round-trip, no "one reviewer only" migration constraint. `alembic check` must stay drift-free before and after.

---

## Current-state corrections (verified 2026-07-06 against HEAD cc0323d)

The candidate descriptions were point-in-time; verification re-anchored and corrected them:

1. **CF-2 is not a one-line fix.** In async SQLAlchemy, `await session.rollback()` **expires every ORM object** (unlike commit, which `expire_on_commit=False` suppresses). The recovery handler then reads `run.user_id` / `run.workspace_id` (`graph_executor.py:362,369`) and `transition_run` reads `run.status` (`execution_state.py:119`) — expired-attribute access triggers implicit IO, which async SQLAlchemy **cannot** do lazily and raises `MissingGreenlet`. Additionally, the `running` transition is only **flushed** (line 283), never committed before the DAG (`audit.log` only flushes — `audit.py:57`), so a rollback reverts `run.status` to its last *committed* value — for background runs that is `pending`, and **`RUN_TRANSITIONS["pending"] = {"running","cancelled","blocked"}` does not contain `failed`** → `transition_run(run,"failed")` would raise `InvalidTransitionError`. The correct fix is therefore `rollback()` → `refresh(run)` → a **guarded two-hop transition**.
2. **CF-5's own writer comment is stale.** `dag_runner.py:441-443` claims the `auto_executed` checkpoint "Now finally has a reader" — this is **false**. The deferred-verification tick reads `step.output_data["verification"]` (`deferred_verification_tick.py:60,157`), a different JSONB location written by `build_verification_meta` (`dag_runner.py:544`). The `trust_gate.py` docstring (lines 252-257) already correctly states there is no reader. Confirmed **DEAD** (only readers are test assertions).
3. **CF-4 is genuinely untested at the bundle level.** No test constructs `EvidenceBundleService` or calls `build_for_run`. The `RunDetailStore.get_policy_decision` round-trip is proven (`test_run_detail_store_db.py::test_upsert_policy_then_context_shares_one_row`), but nothing pins the `policy_decision → route_info` alias in `build_for_run` (`evidence_bundle.py:90-94`).
4. **CF-3 is a clean lift.** `_load_context_pack` (`plan.py:25-35`) already does its `RunDetailStore` import *function-locally*, so moving it into `_shared.py` adds **no** new module-level dependency/cycle to `_shared.py`. Four import sites: `plan.py` (definer→consumer), `summary.py:102` (the private cross-module import being removed), and two tests (`test_run_detail_dual_read_db.py:67,90`).

---

## Design decisions

- **D1 (CF-1 fix idiom):** Mirror the Step-4 review fix (`entity_facts/reconciliation.py:36-50`, commit `5853a2a`) — wrap only the best-effort `record_approval_decision` call in `async with db.begin_nested():` **inside** the existing `try/except`. On a failed inner flush the SAVEPOINT rolls back only the nested transaction; the surrounding `except Exception` still swallows (best-effort). The legitimate `transition_step(step,"completed")` (line 81) stays in the outer transaction and persists.
- **D2 (CF-2 fix shape):** `rollback()` → `refresh(run)` → guarded two-hop. The guard `if "failed" not in RUN_TRANSITIONS.get(run.status, set()): transition_run(run, "running")` then `transition_run(run, "failed")` reaches `failed` through a legal path from every realistic reverted status (pending→running→failed; paused→running→failed; awaiting_approval/awaiting_input/awaiting_reauth/partially_completed→failed directly). Requires importing `RUN_TRANSITIONS`.
- **D3 (CF-2 scope):** Fix only the `except Exception` handler (lines 352-370). The `except asyncio.TimeoutError` handler (338-351) is a **different failure mode** (a cancelled DAG coroutine, not a failed flush) and is intentionally out of scope — expanding it would need its own reproduction. Note left in code review, not changed.
- **D4 (test faithfulness):** Both CF-1 and CF-2 regression tests reproduce a **real** `PendingRollbackError` by poisoning a **real** `AsyncSession` with a guaranteed-failing SQL statement (`SELECT <nonexistent column>`), exactly as the Step-4 reconcile test does. A `raise ValueError` would NOT reproduce the bug (it never touches the DB connection). No mocked sessions for these two — a hand-crafted mock that "requires rollback" would be circular.
- **D5 (CF-2 test isolation):** `execute_run` does substantial pre/post-try DB work. The CF-2 test patches the peripheral writers (`_get_all_steps`, `_emit_event`, `_emit_surface_update`, `_audit.log`, `_finalize_trace`, `_reconcile_plan_status`) to no-ops so the test isolates exactly the rollback→refresh→two-hop→commit recovery path against the real session. The poison is injected via a patched `_execute_dag`.
- **D6 (CF-3 no new test):** Pure relocation, behavior identical. The existing `test_run_detail_dual_read_db.py` tests ARE the characterization tests; they must stay green (with their import path updated to `_shared`). No new test file.
- **D7 (CF-5 verification, not new test):** Removal is proven-safe by the extraction grep (zero production readers). Verification = the two edited test files still pass + a fresh grep shows zero remaining `remember_auto_executed` / `checkpoint["auto_executed"]` references (the distinct `notify_auto_executed` family is KEPT).

---

## In-flight posture

- Branch `rebuild/first-principles`, HEAD `cc0323d`. Do **not** push or merge to main.
- Per-task commit (conventional-commit, no `Co-Authored-By`).
- Tasks are independent; execute in listed order (CF-1 → CF-5). CF-2 and CF-5 both touch `graph_executor.py` but in disjoint regions (≈353 vs ≈608) — sequential execution avoids any conflict.
- Full gate after each task: `uv run pytest tests/ --ignore=tests/e2e` (or the single new file for speed during red/green, then the full gate before commit).

---

## File structure

| File | Change | Task |
|---|---|---|
| `backend/src/services/scheduler/deferred_verification_tick.py` | Wrap `record_approval_decision` in `begin_nested()` | CF-1 |
| `backend/tests/test_deferred_verification_savepoint_db.py` | **Create** — SAVEPOINT regression test | CF-1 |
| `backend/src/services/graph_executor.py` | `rollback()`+`refresh()`+two-hop in `except Exception`; import `RUN_TRANSITIONS` | CF-2 |
| `backend/tests/test_graph_executor_recovery_rollback_db.py` | **Create** — poisoned-session recovery test | CF-2 |
| `backend/src/services/surface_detail_builders/_shared.py` | **Add** `_load_context_pack` | CF-3 |
| `backend/src/services/surface_detail_builders/plan.py` | Remove def, import from `_shared` | CF-3 |
| `backend/src/services/surface_detail_builders/summary.py` | Drop private cross-module import; import from `_shared` | CF-3 |
| `backend/tests/test_run_detail_dual_read_db.py` | Re-point 2 imports to `_shared` | CF-3 |
| `backend/tests/test_evidence_bundle_route_info_db.py` | **Create** — bundle-level route_info test | CF-4 |
| `backend/src/services/trust_gate.py` | Remove `remember_auto_executed` + prune docstring | CF-5 |
| `backend/src/services/graph_executor.py` | Remove `_remember_auto_executed` facade | CF-5 |
| `backend/src/services/dag_runner.py` | Remove call site + stale comment | CF-5 |
| `backend/src/services/step_graph_store.py` | Prune `auto_executed` mention in comment | CF-5 |
| `backend/tests/test_trust_feedback.py` | Remove dead auto_executed tests + helper | CF-5 |
| `backend/tests/test_dag_runner_reauth.py` | Remove mock stub line | CF-5 |

---

## Task 1 (CF-1): SAVEPOINT-wrap the deferred trust increment

**Files:**
- Modify: `backend/src/services/scheduler/deferred_verification_tick.py:87-98`
- Test: `backend/tests/test_deferred_verification_savepoint_db.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_deferred_verification_savepoint_db.py`:

```python
"""CF-1: the deferred-verification tick's best-effort trust increment must roll back
only its own SAVEPOINT on failure, never poison the shared session's later flush/commit.
Regression for the swallowed-flush-failure pattern (mirror of the Step-4 reconcile fix)."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.task_graph import TaskRun, TaskStep
from src.models.users import User, Workspace


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _run_env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"cf1-{suffix}@example.com", display_name="cf1"))
            db.add(Workspace(workspace_id=workspace_id, name="cf1-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def _seed_run_and_step(factory, ws, uid) -> tuple[str, str]:
    run_id = f"run_{ULID()}"
    step_id = f"step_{ULID()}"
    async with factory() as db:
        db.add(TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="running"))
        db.add(
            TaskStep(
                step_id=step_id,
                run_id=run_id,
                workspace_id=ws,
                task_id="t1",
                status="completed_unverified",
                input_data={},
                output_data={"verification": {"capability": "email.send", "risk_level": "high"}},
            )
        )
        await db.commit()
    return run_id, step_id


async def test_confirmed_trust_write_failure_does_not_poison_the_session():
    """A failed record_approval_decision must roll back only its SAVEPOINT and leave the
    shared session usable: _apply_recheck must not raise, the outer commit must succeed
    (no PendingRollbackError), and the step must still be marked completed."""
    from unittest.mock import patch

    from src.services.scheduler import deferred_verification_tick as tick
    from src.services.scheduler.deferred_verification_tick import _apply_recheck
    from src.services.verification.readback import VerifyVerdict

    async with _run_env() as (factory, ws, uid):
        run_id, step_id = await _seed_run_and_step(factory, ws, uid)
        async with factory() as db:
            run = (
                await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
            ).scalar_one()
            step = (
                await db.execute(select(TaskStep).where(TaskStep.step_id == step_id))
            ).scalar_one()

            # A DB-level error inside the best-effort trust write leaves the session in a
            # failed-transaction state — the same shape as a failed flush. Without the
            # surrounding SAVEPOINT that state escapes and the outer commit below raises
            # PendingRollbackError.
            async def _boom(_db, *args, **kwargs):
                await _db.execute(text("SELECT cf1_poison_nonexistent_column"))

            with patch.object(tick, "record_approval_decision", _boom):
                # Must NOT raise (best-effort swallow) ...
                await _apply_recheck(db, run, step, VerifyVerdict.CONFIRMED, notifier=None)

            # ... and the outer session must still be usable: commit succeeds and the step
            # is durably completed. Without the SAVEPOINT this raises PendingRollbackError.
            await db.commit()

        async with factory() as db:
            reloaded = (
                await db.execute(select(TaskStep).where(TaskStep.step_id == step_id))
            ).scalar_one()
            assert reloaded.status == "completed"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_deferred_verification_savepoint_db.py -v`
Expected: FAIL — `_apply_recheck` (or the outer `await db.commit()`) raises `sqlalchemy.exc.PendingRollbackError` because the swallowed `record_approval_decision` failure aborted the session and `await db.flush()` (line 98) / the commit re-raises.
(If Postgres is down the test SKIPS — bring the stack up; a skip does not prove the bug.)

- [ ] **Step 3: Apply the fix**

In `backend/src/services/scheduler/deferred_verification_tick.py`, replace the best-effort block (lines 87-97, the `if capability:` block up to its `except`) with the SAVEPOINT-wrapped form:

```python
        if capability:
            try:
                # SAVEPOINT: a failed trust write (e.g. a flush inside
                # record_approval_decision) must roll back only this nested
                # transaction, never poison the shared session's later flush/commit
                # (mirrors the Step-4 reconcile fix). A best-effort trust increment
                # must never fail an otherwise-successful confirmation.
                async with db.begin_nested():
                    await record_approval_decision(
                        db,
                        run.workspace_id or "",
                        capability,
                        meta.get("risk_level", "high"),
                        "approved",
                    )
            except Exception:
                logger.debug("Deferred trust increment failed for %s", step.step_id, exc_info=True)
        await db.flush()
        return
```

(The `await db.flush()` and `return` on lines 98-99 are unchanged — do not duplicate or remove them.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_deferred_verification_savepoint_db.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full gate**

Run: `uv run pytest tests/ --ignore=tests/e2e`
Expected: 3106 passed / 18 skipped (baseline + 1 new test).

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/scheduler/deferred_verification_tick.py backend/tests/test_deferred_verification_savepoint_db.py
git commit -m "fix(rebuild): SAVEPOINT-wrap deferred trust increment so a failed write can't poison the tick commit (CF-1)"
```

---

## Task 2 (CF-2): rollback + re-hydrate in the run-failure recovery handler

**Files:**
- Modify: `backend/src/services/graph_executor.py:29` (import) and `:352-370` (except handler)
- Test: `backend/tests/test_graph_executor_recovery_rollback_db.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_graph_executor_recovery_rollback_db.py`:

```python
"""CF-2: when a durable state-recording event flush inside the DAG aborts the shared
session, execute_run's recovery handler must roll back + re-hydrate + mark the run failed
instead of raising PendingRollbackError (a false run failure). Regression for the Step-5
holistic-review carry."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.task_graph import TaskRun
from src.models.users import User, Workspace


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _run_env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"cf2-{suffix}@example.com", display_name="cf2"))
            db.add(Workspace(workspace_id=workspace_id, name="cf2-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def _seed_run(factory, ws, uid) -> str:
    run_id = f"run_{ULID()}"
    async with factory() as db:
        # source="plan" → no background timeout wrapper; status="pending" so the recovery
        # path must re-establish an in-flight status before it can legally fail the run.
        db.add(TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="pending"))
        await db.commit()
    return run_id


async def test_execute_run_recovers_from_poisoned_session():
    """A DAG-time durable flush that aborts the session must not surface as a
    PendingRollbackError: execute_run rolls back, re-hydrates the run, and marks it
    failed. Without the fix, the commit at the tail of execute_run raises."""
    from src.services.graph_executor import GraphExecutor

    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_run(factory, ws, uid)
        async with factory() as db:
            with patch("src.services.graph_executor.get_anthropic_client"):
                executor = GraphExecutor(get_settings(), db)

            # Silence peripheral DB writers so the test isolates the recovery path.
            executor._get_all_steps = AsyncMock(return_value=[])
            executor._emit_event = AsyncMock()
            executor._emit_surface_update = AsyncMock()
            executor._audit.log = AsyncMock()
            executor._finalize_trace = AsyncMock()
            executor._reconcile_plan_status = AsyncMock()

            # Poison the shared session the way a failed durable event flush would: a
            # DB-level error aborts the transaction. Without the rollback fix the recovery
            # handler's commit raises PendingRollbackError.
            async def _poison_and_raise(run, **kwargs):
                await db.execute(text("SELECT cf2_poison_nonexistent_column"))

            executor._execute_dag = _poison_and_raise

            # Must NOT raise.
            await executor.execute_run(run_id)

        async with factory() as db:
            reloaded = (
                await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
            ).scalar_one()
            assert reloaded.status == "failed"
            assert reloaded.error and reloaded.error.get("type") == "execution_error"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_graph_executor_recovery_rollback_db.py -v`
Expected: FAIL — `execute_run` raises `sqlalchemy.exc.PendingRollbackError` from `await self._db.commit()` (line 385) because the poisoned session was never rolled back; the run is left un-persisted (not `failed`).

- [ ] **Step 3: Apply the fix — import**

In `backend/src/services/graph_executor.py`, update the `execution_state` import (line 29) to add `RUN_TRANSITIONS`:

```python
from src.services.execution_state import (
    RUN_TRANSITIONS,
    TERMINAL_SUCCESS,
    transition_run,
    transition_step,
)
```

- [ ] **Step 4: Apply the fix — recovery handler**

In `backend/src/services/graph_executor.py`, replace the head of the `except Exception as exc:` handler (lines 352-353, i.e. the `except Exception as exc:` line and the `transition_run(run, "failed")` immediately under it) with:

```python
            except Exception as exc:
                # A durable state-recording event flush inside the DAG (§4.8,
                # SurfaceEmitter.emit_event(durable=True)) can transiently fail and abort
                # the shared session. Roll it back first so this mark-failed path commits
                # cleanly instead of raising PendingRollbackError on the commit below.
                # rollback() expires ORM state AND reverts the flushed-but-uncommitted
                # "running" transition, so re-hydrate the run and re-establish an in-flight
                # status before failing it (the machine forbids e.g. pending→failed).
                await self._db.rollback()
                await self._db.refresh(run)
                if "failed" not in RUN_TRANSITIONS.get(run.status, set()):
                    transition_run(run, "running")
                transition_run(run, "failed")
```

(Everything from `run.completed_at = datetime.now(timezone.utc)` down through the `await self._emit_event("run.failed", ...)` call is unchanged. The `except asyncio.TimeoutError` handler above is intentionally NOT modified — see design note D3.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_graph_executor_recovery_rollback_db.py -v`
Expected: PASS — the run is durably `failed` with `error.type == "execution_error"`.

- [ ] **Step 6: Run the full gate**

Run: `uv run pytest tests/ --ignore=tests/e2e`
Expected: 3107 passed / 18 skipped (baseline + CF-1 + CF-2). Confirm no existing GraphExecutor test regressed (the healthy-failure path — where the session is NOT poisoned — still marks the run failed; `refresh` on a non-poisoned session reloads the flushed `running` row, and `running→failed` is legal, so behavior is unchanged for normal failures).

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/graph_executor.py backend/tests/test_graph_executor_recovery_rollback_db.py
git commit -m "fix(rebuild): rollback + re-hydrate in run-failure recovery so a poisoned session fails the run cleanly (CF-2)"
```

---

## Task 3 (CF-3): relocate `_load_context_pack` into `_shared.py`

**Files:**
- Modify: `backend/src/services/surface_detail_builders/_shared.py` (add function)
- Modify: `backend/src/services/surface_detail_builders/plan.py:13-20,25-35` (import instead of define)
- Modify: `backend/src/services/surface_detail_builders/summary.py:13-20,102` (import from `_shared`, drop cross-module import)
- Modify: `backend/tests/test_run_detail_dual_read_db.py:67,90` (re-point import)

- [ ] **Step 1: Establish the green baseline (characterization)**

Run: `uv run pytest tests/test_run_detail_dual_read_db.py -v`
Expected: PASS (2 tests exercising `_load_context_pack` — these are the characterization tests; behavior must be identical after the move).

- [ ] **Step 2: Add `_load_context_pack` to `_shared.py`**

Append to `backend/src/services/surface_detail_builders/_shared.py` (after the existing helpers). Copy the body verbatim from `plan.py:25-35` — note the function-local `RunDetailStore` import stays inside the function (no new module-level dependency for `_shared.py`):

```python
async def _load_context_pack(db, run) -> dict:
    """Read the context pack from RunDetailStore (Step 5, D-C4). Post-contract the
    detail table is authoritative; a run with no detail row renders with an empty pack."""
    if run is None:
        return {}
    from src.services.run_detail_store import RunDetailStore

    pack = await RunDetailStore(db).get_context_pack(run.run_id)
    if pack is not None:
        return pack
    return {}
```

- [ ] **Step 3: Update `plan.py` — remove the def, import from `_shared`**

In `backend/src/services/surface_detail_builders/plan.py`:
1. Delete the `_load_context_pack` definition (lines 25-35, including its two blank lines of separation as appropriate).
2. Add `_load_context_pack` to the existing `from ._shared import (...)` block (keep alphabetical-ish grouping consistent with the file):

```python
from ._shared import (
    _empty_tab,
    _extract_run_id,
    _format_ts,
    _get_step_desc,
    _load_context_pack,
    _section,
    _truncate,
)
```

(The call site at `plan.py:99` `ctx = await _load_context_pack(db, run)` is unchanged.)

- [ ] **Step 4: Update `summary.py` — drop the private cross-module import**

In `backend/src/services/surface_detail_builders/summary.py`:
1. Delete the function-local import at line 102 (`from src.services.surface_detail_builders.plan import _load_context_pack`).
2. Add `_load_context_pack` to the module-level `from ._shared import (...)` block:

```python
from ._shared import (
    _empty_tab,
    _extract_run_id,
    _format_ts,
    _get_payload,
    _load_context_pack,
    _section,
    _truncate,
)
```

(The call site — now just `ctx = await _load_context_pack(db, run)` at what was line 104 — is unchanged.)

- [ ] **Step 5: Re-point the two test imports**

In `backend/tests/test_run_detail_dual_read_db.py`, change **both** function-local imports (originally at lines 67 and 90) from:

```python
from src.services.surface_detail_builders.plan import _load_context_pack
```
to:
```python
from src.services.surface_detail_builders._shared import _load_context_pack
```

- [ ] **Step 6: Run the characterization tests + import smoke**

```bash
uv run pytest tests/test_run_detail_dual_read_db.py -v
uv run python -c "from src.services.surface_detail_builders import plan, summary, _shared; assert hasattr(_shared, '_load_context_pack'); assert not any(l.strip().startswith('async def _load_context_pack') for l in open('src/services/surface_detail_builders/plan.py'))"
```
Expected: tests PASS; the smoke assertion confirms `_shared` owns the helper and `plan.py` no longer defines it.

- [ ] **Step 7: Run the full gate**

Run: `uv run pytest tests/ --ignore=tests/e2e`
Expected: 3107 passed / 18 skipped (no count change — pure relocation).

- [ ] **Step 8: Commit**

```bash
git add backend/src/services/surface_detail_builders/_shared.py backend/src/services/surface_detail_builders/plan.py backend/src/services/surface_detail_builders/summary.py backend/tests/test_run_detail_dual_read_db.py
git commit -m "refactor(rebuild): relocate _load_context_pack into _shared, drop private cross-module import (CF-3)"
```

---

## Task 4 (CF-4): bundle-level test for `EvidenceBundleService.build_for_run` route_info

**Files:**
- Test: `backend/tests/test_evidence_bundle_route_info_db.py` (create). No production change.

- [ ] **Step 1: Write the test**

Create `backend/tests/test_evidence_bundle_route_info_db.py`:

```python
"""CF-4: EvidenceBundleService.build_for_run must resolve route_info from RunDetailStore's
policy_decision (positive path) and fall back to None when no detail row exists. Pins the
policy_decision→route_info alias the store round-trip test does not cover."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.task_graph import TaskRun
from src.models.users import User, Workspace
from src.services.evidence_bundle import EvidenceBundleService
from src.services.run_detail_store import RunDetailStore


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _run_env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"cf4-{suffix}@example.com", display_name="cf4"))
            db.add(Workspace(workspace_id=workspace_id, name="cf4-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def _seed_run(factory, ws, uid) -> str:
    run_id = f"run_{ULID()}"
    async with factory() as db:
        db.add(TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="pending"))
        await db.commit()
    return run_id


async def test_build_for_run_resolves_route_info_from_store():
    """build_for_run threads the persisted policy_decision into EvidenceBundle.route_info."""
    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_run(factory, ws, uid)
        async with factory() as db:
            await RunDetailStore(db).upsert_policy_decision(run_id, ws, {"decision": "auto_execute"})
            await db.commit()
        async with factory() as db:
            bundle = await EvidenceBundleService(db, ws).build_for_run(run_id)
        assert bundle.route_info == {"decision": "auto_execute"}


async def test_build_for_run_route_info_none_when_no_detail_row():
    """With no detail row, the store returns None and route_info falls back to None."""
    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_run(factory, ws, uid)  # no upsert_policy_decision
        async with factory() as db:
            bundle = await EvidenceBundleService(db, ws).build_for_run(run_id)
        assert bundle.route_info is None


async def test_build_for_run_absent_run_returns_empty_bundle():
    """A run not found in this workspace short-circuits to an empty bundle (route_info=None),
    never querying the store."""
    async with _run_env() as (factory, ws, _uid):
        async with factory() as db:
            bundle = await EvidenceBundleService(db, ws).build_for_run(f"run_{ULID()}")
        assert bundle.route_info is None
        assert bundle.sources == []
```

- [ ] **Step 2: Run the test to verify it passes (it exercises existing, correct code)**

Run: `uv run pytest tests/test_evidence_bundle_route_info_db.py -v`
Expected: PASS (3 tests). This is a coverage-adding test for already-correct code — it should pass immediately. If it FAILS, do NOT edit the test to make it pass: escalate NEEDS_CONTEXT (either the harness setup is wrong or `build_for_run` has a real defect the plan mis-modeled). Verify `EvidenceBundle.sources` defaults to `[]` and `route_info` to `None` on the empty-bundle path before adjusting the third assertion.

- [ ] **Step 3: Run the full gate**

Run: `uv run pytest tests/ --ignore=tests/e2e`
Expected: 3110 passed / 18 skipped (baseline + CF-1 + CF-2 + 3 CF-4 tests).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_evidence_bundle_route_info_db.py
git commit -m "test(rebuild): pin EvidenceBundle.build_for_run route_info resolution + fallback (CF-4)"
```

---

## Task 5 (CF-5): remove the dead `auto_executed` checkpoint writer

**Files:**
- Modify: `backend/src/services/trust_gate.py` (remove `remember_auto_executed`, prune docstring refs)
- Modify: `backend/src/services/graph_executor.py` (remove `_remember_auto_executed` facade, ~608-610)
- Modify: `backend/src/services/dag_runner.py:441-444` (remove call + stale comment)
- Modify: `backend/src/services/step_graph_store.py:250` (prune `auto_executed` mention in comment)
- Modify: `backend/tests/test_trust_feedback.py` (remove dead tests + `_make_run(auto_executed=...)` support)
- Modify: `backend/tests/test_dag_runner_reauth.py:92` (remove mock stub line)

> **KEEP the `notify_auto_executed` family** (`trust_gate.py`, `dag_runner.py:403`, `graph_executor.py:592-600`) — it is a distinct post-execution *notification* concept, unrelated to the dead checkpoint key. Do NOT remove it.

- [ ] **Step 1: Re-confirm zero production readers (verify-don't-trust)**

```bash
cd backend
grep -rn "remember_auto_executed" src/ tests/
grep -rn '"auto_executed"\|get("auto_executed")\|\[.auto_executed.\]' src/ tests/
```
Expected: every `src/` hit is a **writer** (`trust_gate.remember_auto_executed` / its `.get("auto_executed")` self-append at `trust_gate.py:259` / the `graph_executor` facade / the `dag_runner:444` call) or a **comment** (`step_graph_store.py:250`, `trust_gate` docstring). The only `.get("auto_executed")`/subscript reads outside the writer itself are **test assertions** in `test_trust_feedback.py`. If ANY non-test, non-writer production read exists, STOP and escalate NEEDS_CONTEXT — the extraction said DEAD; a live reader means it is not.

- [ ] **Step 2: Remove the producer method in `trust_gate.py`**

Delete `remember_auto_executed` (the method at `trust_gate.py:249-262`, including its docstring). Then prune the module docstring / header references to the `auto_executed` audit trail (`trust_gate.py:10-12`) so the file no longer describes a trail it does not write. Leave `notify_auto_executed` untouched.

- [ ] **Step 3: Remove the facade in `graph_executor.py`**

Delete the `_remember_auto_executed` facade (`graph_executor.py:608-610`):

```python
    def _remember_auto_executed(self, run: TaskRun, capability: str, risk_level: str) -> None:
        """Facade → TrustGate.remember_auto_executed."""
        self._trust_gate.remember_auto_executed(run, capability, risk_level)
```

- [ ] **Step 4: Remove the call site + stale comment in `dag_runner.py`**

Delete the call and its (false) comment at `dag_runner.py:441-444`:

```python
            # Record the auto-executed (capability, risk_level) regardless, so the
            # deferred-read tick can fire the increment when a completed_unverified
            # step is later confirmed (Task 7/9). Now finally has a reader.
            self._trust_gate.remember_auto_executed(run, capability, risk_level)
```

Verify the surrounding auto-execute block still reads coherently after removal (the deferred tick's real signal — `build_verification_meta(...)` writing `step.output_data["verification"]` — is elsewhere in this method and is NOT touched).

- [ ] **Step 5: Prune the comment in `step_graph_store.py`**

At `step_graph_store.py:250`, remove the `auto_executed` clause from the JSONB-sharing comment (keep the `verification` clause — that concept is live). E.g. change "written by other paths (the ``auto_executed`` trust audit trail, the ``verification`` verdict)" to reference only the `verification` verdict.

- [ ] **Step 6: Remove the dead tests**

In `backend/tests/test_trust_feedback.py`:
- Delete the test that exercises `_remember_auto_executed` (the `test_*` at ~114-131 that calls `executor._remember_auto_executed(...)` and asserts `run.checkpoint["auto_executed"] == [...]`).
- Delete `TestCheckpointPreservation::test_checkpoint_preserves_auto_executed` (~235-253).
- Remove the `auto_executed` parameter/branch from the `_make_run(...)` helper (~173-183) if it exists solely to seed that key; if `_make_run` is shared by other surviving tests, keep the helper but drop only the `auto_executed` seeding.

In `backend/tests/test_dag_runner_reauth.py`:
- Delete the mock stub line at `:92` (`trust_gate.remember_auto_executed = MagicMock()`).

- [ ] **Step 7: Confirm the concept is fully gone**

```bash
cd backend
grep -rn "remember_auto_executed" src/ tests/       # expect: NO matches
grep -rn '"auto_executed"' src/ tests/               # expect: NO matches (notify_auto_executed is a different string)
grep -rn "notify_auto_executed" src/                 # expect: STILL PRESENT (unchanged)
```

- [ ] **Step 8: Run the full gate**

Run: `uv run pytest tests/ --ignore=tests/e2e`
Expected: PASS. Count drops by the number of deleted tests (baseline 3110 after CF-1/CF-2/CF-4 minus the ~2 removed trust-feedback tests, so ≈3108 / 18 skipped). Confirm no import errors from the removed facade/method.

- [ ] **Step 9: Commit**

```bash
git add backend/src/services/trust_gate.py backend/src/services/graph_executor.py backend/src/services/dag_runner.py backend/src/services/step_graph_store.py backend/tests/test_trust_feedback.py backend/tests/test_dag_runner_reauth.py
git commit -m "refactor(rebuild): remove dead auto_executed checkpoint writer (no reader; deferred tick uses output_data.verification) (CF-5)"
```

---

## Self-review checklist (run before dispatching implementers)

1. **Spec coverage:** CF-1 (Task 1), CF-2 (Task 2), CF-3 (Task 3), CF-4 (Task 4), CF-5 (Task 5) — all five candidates have a task. ✅
2. **Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N" — every code block is verbatim. ✅
3. **Type/name consistency:** `RUN_TRANSITIONS` imported before use (Task 2 Step 3); `_load_context_pack` signature identical across move (Task 3); `record_approval_decision` patched at its import module `deferred_verification_tick` (Task 1). ✅
4. **No migrations** → `alembic check` must stay drift-free; no live-DB round-trip; no "one reviewer" migration constraint. ✅

---

## Review strategy (for the executor)

- **Task 2 (CF-2) and Task 5 (CF-5)** are blast-radius (recovery-path behavior change; multi-file removal) → **2-stage parallel review** (spec-conformance + code-quality) on the frozen commit.
- **Tasks 1, 3, 4** are small/additive → **single combined review** each.
- **Final holistic review** across all five commits: confirm the full gate is green, `alembic check` is drift-free, no `remember_auto_executed`/`"auto_executed"` residue, and the two SAVEPOINT/rollback fixes both have a real-DB regression test that actually fails without the fix (implementers must show the RED run, not just the GREEN).
