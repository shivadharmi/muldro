"""Real-DB proof that a completed_unverified step counts as done: it unblocks a
dependent step (DAG readiness) and drives run progress toward 100%. Skips when
Postgres is unreachable."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.task_graph import TaskRun, TaskStep
from src.models.users import User, Workspace
from src.services.runtime_projection import RuntimeProjectionService
from src.services.step_graph_store import StepGraphStore


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
    user_id, workspace_id = f"usr_{suffix}", f"ws_{suffix}"
    run_id = f"run_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"ts-{suffix}@example.com", display_name="ts"))
            db.add(Workspace(workspace_id=workspace_id, name="ts-ws", owner_user_id=user_id))
            # Flush parents first: TaskRun/TaskStep FK workspace_id has no ORM
            # relationship() to order the insert, so the parents must exist before the run.
            await db.flush()
            db.add(
                TaskRun(
                    run_id=run_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    plan_id=None,  # FK to plans; nullable, no plan seeded. Status is under test.
                    status="running",
                )
            )
            await db.commit()
        yield factory, run_id, workspace_id, user_id, suffix
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_completed_unverified_step_unblocks_dependent():
    async with _run_env() as (factory, run_id, workspace_id, user_id, suffix):
        step_a_id = f"stp_a_{suffix}"
        async with factory() as db:
            db.add(
                TaskStep(
                    step_id=step_a_id,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id="A",
                    status="completed_unverified",
                    depends_on=[],
                )
            )
            db.add(
                TaskStep(
                    step_id=f"stp_b_{suffix}",
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id="B",
                    status="pending",
                    # get_ready_steps matches depends_on against step_ids (see
                    # StepGraphStore build: depends_on holds resolved step_ids, not task_ids).
                    depends_on=[step_a_id],
                )
            )
            await db.commit()
            store = StepGraphStore(db)
            ready = await store.get_ready_steps(run_id)
            # B depends on A; A is completed_unverified (a success) -> B is ready.
            assert any(s.task_id == "B" for s in ready), "unverified-done step failed to unblock B"


async def test_progress_counts_completed_unverified():
    async with _run_env() as (factory, run_id, workspace_id, user_id, suffix):
        async with factory() as db:
            db.add(
                TaskStep(
                    step_id=f"stp_x_{suffix}",
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id="X",
                    status="completed",
                    depends_on=[],
                )
            )
            db.add(
                TaskStep(
                    step_id=f"stp_y_{suffix}",
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id="Y",
                    status="completed_unverified",
                    depends_on=[],
                )
            )
            await db.commit()
            proj = RuntimeProjectionService(db, workspace_id)
            active = await proj.get_active_runs()
            row = next(r for r in active if r["run_id"] == run_id)
            assert row["progress_pct"] == 100, f"unverified step excluded from progress: {row}"
