"""rebuild_run_projection reconstructs a run's status/progress from the runtime_events
log ALONE, ordered by the monotonic seq, and matches get_active_runs() for the same run
(Step 5 §4.8, D-B2). Ordering by seq — not occurred_at — is the point: the test writes
events with a tied occurred_at and the rebuild still folds them in the right order."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.runtime_event import RuntimeEvent
from src.models.task_graph import TaskRun, TaskStep
from src.models.users import User, Workspace
from src.services.runtime_projection import RuntimeProjectionService


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
            db.add(User(user_id=user_id, email=f"s5-{suffix}@example.com", display_name="s5"))
            db.add(Workspace(workspace_id=workspace_id, name="s5-ws", owner_user_id=user_id))
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


async def test_rebuild_matches_live_read():
    async with _run_env() as (factory, ws, uid):
        run_id = f"run_{ULID()}"
        s1, s2 = f"step_{ULID()}", f"step_{ULID()}"
        tied = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)  # same occurred_at for all
        async with factory() as db:
            db.add(
                TaskRun(
                    run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="running"
                )
            )
            db.add(
                TaskStep(
                    step_id=s1, run_id=run_id, workspace_id=ws, task_id="t1", status="completed"
                )
            )
            db.add(
                TaskStep(step_id=s2, run_id=run_id, workspace_id=ws, task_id="t2", status="running")
            )
            for et, sid, payload in [
                ("step_started", s1, {"run_id": run_id, "step_id": s1}),
                ("step_completed", s1, {"run_id": run_id, "step_id": s1, "status": "completed"}),
                ("step_started", s2, {"run_id": run_id, "step_id": s2}),
            ]:
                db.add(
                    RuntimeEvent(
                        event_id=f"revt_{ULID()}",
                        workspace_id=ws,
                        run_id=run_id,
                        step_id=sid,
                        event_type=et,
                        payload=payload,
                        occurred_at=tied,
                    )
                )
            await db.commit()

        async with factory() as db:
            svc = RuntimeProjectionService(db, ws)
            rebuilt = await svc.rebuild_run_projection(run_id)
            live = next(r for r in await svc.get_active_runs() if r["run_id"] == run_id)

        assert rebuilt["total_steps"] == live["total_steps"] == 2
        assert rebuilt["completed_steps"] == live["completed_steps"] == 1
        assert rebuilt["progress_pct"] == live["progress_pct"] == 50


async def test_rebuild_final_status_from_run_completed():
    async with _run_env() as (factory, ws, uid):
        run_id = f"run_{ULID()}"
        async with factory() as db:
            db.add(
                RuntimeEvent(
                    event_id=f"revt_{ULID()}",
                    workspace_id=ws,
                    run_id=run_id,
                    event_type="run_completed",
                    payload={"run_id": run_id, "status": "partially_completed"},
                )
            )
            await db.commit()
        async with factory() as db:
            svc = RuntimeProjectionService(db, ws)
            rebuilt = await svc.rebuild_run_projection(run_id)
        assert rebuilt["status"] == "partially_completed"


async def test_rebuild_orders_by_seq_not_occurred_at():
    """Two run-terminal events whose seq order is the REVERSE of their occurred_at
    order: the last-writer-wins status fold must follow seq (server-monotonic total
    order), NOT occurred_at. This test fails if the projector orders by occurred_at."""
    async with _run_env() as (factory, ws, uid):
        run_id = f"run_{ULID()}"
        later = datetime(2026, 7, 6, 12, 0, 5, tzinfo=timezone.utc)
        earlier = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
        async with factory() as db:
            # Inserted FIRST -> lower seq, but LATER occurred_at.
            db.add(
                RuntimeEvent(
                    event_id=f"revt_{ULID()}",
                    workspace_id=ws,
                    run_id=run_id,
                    event_type="run_completed",
                    payload={"run_id": run_id, "status": "completed"},
                    occurred_at=later,
                )
            )
            await db.flush()  # force this INSERT (assigns the lower seq) before the next
            # Inserted SECOND -> higher seq, but EARLIER occurred_at.
            db.add(
                RuntimeEvent(
                    event_id=f"revt_{ULID()}",
                    workspace_id=ws,
                    run_id=run_id,
                    event_type="run_cancelled",
                    payload={"run_id": run_id, "status": "cancelled"},
                    occurred_at=earlier,
                )
            )
            await db.commit()
        async with factory() as db:
            svc = RuntimeProjectionService(db, ws)
            rebuilt = await svc.rebuild_run_projection(run_id)
        # seq order: completed (lower seq) then cancelled (higher seq) -> last wins = cancelled.
        # occurred_at order would give cancelled (earlier) then completed (later) -> completed.
        assert rebuilt["status"] == "cancelled"
