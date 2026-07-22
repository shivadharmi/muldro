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
        db.add(
            TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="running")
        )
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
            run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
            step = (
                await db.execute(select(TaskStep).where(TaskStep.step_id == step_id))
            ).scalar_one()

            async def _boom(_db, *args, **kwargs):
                await _db.execute(text("SELECT cf1_poison_nonexistent_column"))

            with patch.object(tick, "record_approval_decision", _boom):
                await _apply_recheck(db, run, step, VerifyVerdict.CONFIRMED, notifier=None)

            await db.commit()

        async with factory() as db:
            reloaded = (
                await db.execute(select(TaskStep).where(TaskStep.step_id == step_id))
            ).scalar_one()
            assert reloaded.status == "completed"
