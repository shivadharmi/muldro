"""Dual-read: a reader prefers RunDetailStore and falls back to the old column for a run
with no detail row (resume-across-deploy / pre-cutover gap). Step 5, D-C4."""

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


async def test_reader_falls_back_to_old_column_when_no_detail():
    from src.services.surface_detail_builders.plan import _load_context_pack

    async with _run_env() as (factory, ws, uid):
        run_id = f"run_{ULID()}"
        async with factory() as db:
            # Old-column-only run (no detail row): simulates a pre-cutover / in-flight run.
            db.add(
                TaskRun(
                    run_id=run_id,
                    user_id=uid,
                    workspace_id=ws,
                    source="plan",
                    status="pending",
                    context_pack_json={"task_summary": "legacy"},
                )
            )
            await db.commit()
        async with factory() as db:
            run = await db.get(TaskRun, run_id)
            ctx = await _load_context_pack(db, run)
        assert ctx == {"task_summary": "legacy"}


async def test_reader_prefers_detail_store():
    from src.services.surface_detail_builders.plan import _load_context_pack

    async with _run_env() as (factory, ws, uid):
        run_id = f"run_{ULID()}"
        async with factory() as db:
            db.add(
                TaskRun(
                    run_id=run_id,
                    user_id=uid,
                    workspace_id=ws,
                    source="plan",
                    status="pending",
                    context_pack_json={"task_summary": "OLD"},
                )
            )
            await db.commit()
            await RunDetailStore(db).upsert_context_pack(run_id, ws, {"task_summary": "NEW"})
            await db.commit()
        async with factory() as db:
            run = await db.get(TaskRun, run_id)
            ctx = await _load_context_pack(db, run)
        assert ctx == {"task_summary": "NEW"}  # detail store wins
