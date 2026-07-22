"""CF-2: when a durable state-recording event flush inside the DAG aborts the shared
session, execute_run's recovery handler must roll back + re-hydrate + mark the run failed
instead of raising PendingRollbackError (a false run failure). Regression for the Step-5
holistic-review carry."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

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
        db.add(
            TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="pending")
        )
        await db.commit()
    return run_id


async def _seed_resumable_run(factory, ws, uid) -> str:
    run_id = f"run_{ULID()}"
    async with factory() as db:
        db.add(
            TaskRun(
                run_id=run_id,
                user_id=uid,
                workspace_id=ws,
                source="plan",
                status="awaiting_approval",
            )
        )
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
            executor = GraphExecutor(get_settings(), db)

            executor._get_all_steps = AsyncMock(return_value=[])
            executor._emit_event = AsyncMock()
            executor._emit_surface_update = AsyncMock()
            executor._audit.log = AsyncMock()
            executor._finalize_trace = AsyncMock()
            executor._reconcile_plan_status = AsyncMock()

            async def _poison_and_raise(run, **kwargs):
                await db.execute(text("SELECT cf2_poison_nonexistent_column"))

            executor._execute_dag = _poison_and_raise

            await executor.execute_run(run_id)

        async with factory() as db:
            reloaded = (
                await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
            ).scalar_one()
            assert reloaded.status == "failed"
            assert reloaded.error and reloaded.error.get("type") == "execution_error"


async def test_execute_run_ordinary_failure_preserves_partial_progress():
    """An ordinary (healthy-session) step failure must NOT roll back: the run is marked
    failed IN PLACE, preserving the flushed-but-uncommitted trace_id/started_at. Regression
    for the CF-2 review Critical (unconditional rollback discarded partial progress on the
    common failure path)."""
    from src.services.graph_executor import GraphExecutor

    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_run(factory, ws, uid)
        async with factory() as db:
            executor = GraphExecutor(get_settings(), db)

            executor._get_all_steps = AsyncMock(return_value=[])
            executor._emit_event = AsyncMock()
            executor._emit_surface_update = AsyncMock()
            executor._audit.log = AsyncMock()
            executor._finalize_trace = AsyncMock()
            executor._reconcile_plan_status = AsyncMock()

            async def _healthy_raise(run, **kwargs):
                raise ValueError("ordinary step failure")

            executor._execute_dag = _healthy_raise

            await executor.execute_run(run_id)

        async with factory() as db:
            reloaded = (
                await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
            ).scalar_one()
            assert reloaded.status == "failed"
            # Flushed (not committed) before the DAG; an unconditional rollback would blank
            # them. The is_active gate preserves them on the healthy path.
            assert reloaded.trace_id is not None
            assert reloaded.started_at is not None


async def test_resume_run_recovers_from_poisoned_session():
    """resume_run has the same recovery contract as execute_run: a poisoned session must not
    surface as PendingRollbackError; the run is rolled back, re-hydrated, and marked failed.
    Regression for the CF-2 review find (resume_run twin)."""
    from src.services.graph_executor import GraphExecutor

    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_resumable_run(factory, ws, uid)
        async with factory() as db:
            executor = GraphExecutor(get_settings(), db)

            executor._get_all_steps = AsyncMock(return_value=[])
            executor._emit_event = AsyncMock()
            executor._emit_surface_update = AsyncMock()
            executor._finalize_trace = AsyncMock()
            executor._reconcile_plan_status = AsyncMock()
            executor._checkpoint_trace = AsyncMock()

            async def _poison_and_raise(run, **kwargs):
                await db.execute(text("SELECT resume_poison_nonexistent_column"))

            executor._execute_dag = _poison_and_raise

            await executor.resume_run(run_id)

        async with factory() as db:
            reloaded = (
                await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
            ).scalar_one()
            assert reloaded.status == "failed"
            assert reloaded.error and reloaded.error.get("type") == "resume_error"
