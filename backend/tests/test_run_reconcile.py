"""Reconcile-from-event-log consumer (Step 10C P4).

``reconcile_run_from_events(db, run)`` applies the ``runtime_events`` log onto the
mutable TaskRun/TaskStep truth rows at a durable resume boundary, UP-ONLY, via the
execution state machine. The log is the system-of-record WHERE IT IS AHEAD (a crash
lost the DB completion write); reconcile upgrades a behind step to ``completed`` but
NEVER downgrades a step already in ``TERMINAL_SUCCESS``. Substrate-agnostic (reads
event types only) — this is what lets 10D drain an in-flight DEEP run onto a LEGACY
resume.

Groups:
  1-3, 5  real-Postgres logic tests (guarded, NullPool, seeded User→Workspace FK
          chain, ULID-suffixed ids, teardown in FK order).
  4       deep-gated wiring in ``GraphExecutor._resume_run_body`` (fully mocked,
          mirrors ``tests/test_graph_executor.py::TestResumeRun``): the reconcile is
          called ONLY on the ``deep`` effective-runtime; ``legacy`` keeps the
          byte-identical WARN path.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.runtime_event import RuntimeEvent
from src.models.task_graph import TaskRun, TaskStep
from src.models.users import User, Workspace
from src.services.run_reconcile import reconcile_run_from_events
from tests.conftest import TEST_USER_ID, make_mock_settings

# ─────────────────────────── real-DB harness ────────────────────────────


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


_DB_OK = _db_reachable()


@asynccontextmanager
async def _run_env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"p4-{suffix}@example.com", display_name="p4"))
            db.add(Workspace(workspace_id=workspace_id, name="p4-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                await db.execute(
                    delete(RuntimeEvent).where(RuntimeEvent.workspace_id == workspace_id)
                )
                await db.execute(delete(TaskStep).where(TaskStep.workspace_id == workspace_id))
                await db.execute(delete(TaskRun).where(TaskRun.workspace_id == workspace_id))
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def _seed_run(
    factory,
    ws: str,
    uid: str,
    *,
    run_status: str,
    steps: list[tuple[str, str]],
    events: list[tuple[str, str | None, dict]],
) -> str:
    """Seed one TaskRun + its TaskSteps + a seq-ordered runtime_events sequence.

    ``events`` is a list of ``(event_type, step_id_or_None, payload)`` tuples inserted
    in order so their server ``seq`` follows insertion order (the fold's total-order
    key). ``steps`` is ``(step_id, status)`` for the TaskStep truth rows.
    """
    run_id = f"run_{ULID()}"
    # tied occurred_at for all events → the fold's total order comes from server seq.
    tied = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    async with factory() as db:
        db.add(
            TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status=run_status)
        )
        for sid, status in steps:
            db.add(
                TaskStep(step_id=sid, run_id=run_id, workspace_id=ws, task_id="t", status=status)
            )
        for et, sid, payload in events:
            db.add(
                RuntimeEvent(
                    event_id=f"revt_{ULID()}",
                    workspace_id=ws,
                    run_id=run_id,
                    step_id=sid,
                    event_type=et,
                    payload={**payload, "run_id": run_id},
                    occurred_at=tied,
                )
            )
            await db.flush()  # force server seq in insertion order
        await db.commit()
    return run_id


async def _load(factory, run_id: str) -> tuple[TaskRun, dict[str, TaskStep]]:
    async with factory() as db:
        run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
        steps = (
            (await db.execute(select(TaskStep).where(TaskStep.run_id == run_id))).scalars().all()
        )
        return run, {s.step_id: s for s in steps}


# ═══════════════ 1. log AHEAD → upgrade (cross-substrate: deep events) ═══════════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_log_ahead_upgrades_behind_step():
    """The log shows S1 ``step_completed`` (TERMINAL_SUCCESS) but the DB row is still
    ``running`` (a crash lost the completion write) → reconcile upgrades S1 to
    ``completed`` via transition_step so ``get_ready_steps`` no longer re-picks it. The
    events are tagged ``substrate="deep"``; reconcile still works for a legacy-style
    resume (substrate-agnostic)."""
    async with _run_env() as (factory, ws, uid):
        s1, s2 = f"step_{ULID()}", f"step_{ULID()}"
        run_id = await _seed_run(
            factory,
            ws,
            uid,
            run_status="running",
            steps=[(s1, "running"), (s2, "running")],  # both DB rows BEHIND
            events=[
                ("step_started", s1, {"step_id": s1, "substrate": "deep"}),
                ("tool_call_started", s1, {"step_id": s1, "substrate": "deep"}),
                ("step_completed", s1, {"step_id": s1, "status": "completed", "substrate": "deep"}),
                ("step_started", s2, {"step_id": s2, "substrate": "deep"}),  # S2 still in flight
            ],
        )

        async with factory() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
            summary = await reconcile_run_from_events(db, run)
            await db.commit()

        assert summary["reconciled_steps"] == 1
        assert summary["log_status"] is None  # no run-terminal event
        assert summary["log_completed"] == 1

        _run, steps = await _load(factory, run_id)
        assert steps[s1].status == "completed"  # upgraded from the log
        assert steps[s2].status == "running"  # untouched (log did not complete it)


# ═══════════════ 2. log BEHIND → NO regress (the load-bearing guard) ═══════════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_log_behind_never_regresses_terminal_step():
    """No-regress guard with teeth. S1 is a terminal-success ``completed_unverified``
    that the log ALSO records as completed — reconcile must leave it alone (the log is
    authoritative only where it is AHEAD; the read-back verifier owns unverified→verified,
    not the event log). S2 is ``completed`` in the DB but NOT in the log (log behind) —
    reconcile must never downgrade it. ``completed_unverified`` (rather than plain
    ``completed``) is used because ``completed→completed`` is rejected by the state
    machine anyway, so only an upgradeable terminal-success step gives the
    ``status not in TERMINAL_SUCCESS`` guard an OBSERVABLE regression to catch."""
    async with _run_env() as (factory, ws, uid):
        s1, s2 = f"step_{ULID()}", f"step_{ULID()}"
        run_id = await _seed_run(
            factory,
            ws,
            uid,
            run_status="running",
            steps=[(s1, "completed_unverified"), (s2, "completed")],  # DB AHEAD of the log
            events=[
                ("step_started", s1, {"step_id": s1}),
                # S1 recorded completed in the log (→ log_completed); S2 has no
                # step_completed (log behind on S2).
                ("step_completed", s1, {"step_id": s1, "status": "completed"}),
                ("step_started", s2, {"step_id": s2}),
            ],
        )

        async with factory() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
            summary = await reconcile_run_from_events(db, run)
            await db.commit()

        assert summary["reconciled_steps"] == 0  # nothing upgraded — both already terminal-success

        _run, steps = await _load(factory, run_id)
        assert steps[s1].status == "completed_unverified"  # NEVER upgraded/regressed by the log
        assert steps[s2].status == "completed"  # NEVER downgraded to match the behind log


# ═══════════════ 3. status=None (0.2 failed-branch) → no bogus run status ═══════════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_status_none_leaves_run_for_repick():
    """The 0.2 finding: the dag_runner failed-branch transitions a run to ``failed``
    WITHOUT emitting a run-terminal event, so the fold returns ``status=None``. Even
    when every step row is terminal and completed==total in the log, a ``None`` log
    status must leave the run status untouched (for the DAG to re-pick / complete
    normally) — never crash, never set a bogus terminal status."""
    async with _run_env() as (factory, ws, uid):
        s1, s2 = f"step_{ULID()}", f"step_{ULID()}"
        run_id = await _seed_run(
            factory,
            ws,
            uid,
            run_status="running",
            steps=[(s1, "completed"), (s2, "completed")],  # every step row already terminal
            events=[  # step events but NO run_completed/run_failed → proj status is None
                ("step_started", s1, {"step_id": s1}),
                ("step_completed", s1, {"step_id": s1, "status": "completed"}),
                ("step_started", s2, {"step_id": s2}),
                ("step_completed", s2, {"step_id": s2, "status": "completed"}),
            ],
        )

        async with factory() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
            summary = await reconcile_run_from_events(db, run)  # must not raise
            await db.commit()

        assert summary["log_status"] is None
        assert summary["reconciled_steps"] == 0  # both steps already completed

        run, _steps = await _load(factory, run_id)
        assert run.status == "running"  # NOT corrupted to a bogus terminal status


# ═══════════════ 5. positive run-status upgrade (running → completed) ═══════════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_terminal_log_upgrades_run_status_when_all_steps_terminal():
    """When the log recorded a terminal-success run (``run_completed`` → completed),
    every step row is terminal, and the run isn't already a terminal success, reconcile
    upgrades the run status via transition_run (``running`` → ``completed`` is valid)."""
    async with _run_env() as (factory, ws, uid):
        s1, s2 = f"step_{ULID()}", f"step_{ULID()}"
        run_id = await _seed_run(
            factory,
            ws,
            uid,
            run_status="running",
            steps=[(s1, "completed"), (s2, "completed")],
            events=[
                ("step_started", s1, {"step_id": s1}),
                ("step_completed", s1, {"step_id": s1, "status": "completed"}),
                ("step_started", s2, {"step_id": s2}),
                ("step_completed", s2, {"step_id": s2, "status": "completed"}),
                ("run_completed", None, {"status": "completed"}),
            ],
        )

        async with factory() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
            summary = await reconcile_run_from_events(db, run)
            await db.commit()

        assert summary["log_status"] == "completed"

        run, _steps = await _load(factory, run_id)
        assert run.status == "completed"


# ═══════════════ 4. deep-gated wiring in _resume_run_body (byte-neutral legacy) ═══════════════


def _paused_run_with_mismatch() -> MagicMock:
    now = datetime.now(timezone.utc)
    run = MagicMock()
    run.run_id = "run_001"
    run.status = "paused"
    run.trace_id = "trace_original"
    run.started_at = now
    run.created_at = now
    # checkpoint says step_A completed; the DB step below says otherwise → mismatch.
    run.checkpoint = {"completed_steps": {"step_A": {}}}
    run.error = None
    run.user_id = TEST_USER_ID
    run.workspace_id = "ws_test"
    run.source = "background"
    return run


def _executor_reaching_mismatch(run: MagicMock):
    """A GraphExecutor whose ``_resume_run_body`` reaches the checkpoint-mismatch gate
    with every heavy collaborator mocked out. ``_get_all_steps`` returns a single
    ``running`` step so ``actual_completed`` ({}) != ``cp_completed`` ({step_A})."""
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = run
        db.execute = AsyncMock(return_value=run_result)

        executor = GraphExecutor(make_mock_settings(), db)
        executor._execute_dag = AsyncMock()
        executor._finalize_trace = AsyncMock()
        executor._reconcile_plan_status = AsyncMock()
        executor._checkpoint_trace = AsyncMock()

        step = MagicMock()
        step.step_id = "step_B"
        step.status = "running"
        executor._get_all_steps = AsyncMock(return_value=[step])
        return executor, db


async def test_deep_gate_reconciles_on_resume():
    """Gate ``deep``: the checkpoint/DB mismatch reconciles from the event log —
    ``reconcile_run_from_events`` is awaited exactly once with ``(self._db, run)``."""
    run = _paused_run_with_mismatch()
    executor, db = _executor_reaching_mismatch(run)
    reconcile_spy = AsyncMock(
        return_value={"reconciled_steps": 0, "log_status": None, "log_completed": 0}
    )

    with (
        patch("src.services.graph_executor.transition_run"),
        patch("src.services.runtime_gate.effective_runtime", AsyncMock(return_value="deep")),
        patch("src.services.run_reconcile.reconcile_run_from_events", reconcile_spy),
    ):
        await executor.resume_run("run_001")

    reconcile_spy.assert_awaited_once()
    args = reconcile_spy.await_args.args
    assert args[0] is db  # self._db
    assert args[1] is run
