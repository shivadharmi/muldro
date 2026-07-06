"""EvictionService NULLs expired context_packs (D-C3) while preserving policy_decision
on the same row. runtime_events remains untouched (D-A3)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.task_graph import TaskRun, TaskRunDetail
from src.models.users import User, Workspace
from src.services.eviction_service import EvictionService


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


async def test_expired_context_pack_nulled_policy_preserved():
    async with _run_env() as (factory, ws, uid):
        run_id = f"run_{ULID()}"
        async with factory() as db:
            db.add(
                TaskRun(
                    run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="completed"
                )
            )
            db.add(
                TaskRunDetail(
                    run_id=run_id,
                    workspace_id=ws,
                    policy_decision={"decision": "auto_execute"},
                    context_pack={"task_summary": "stale"},
                    context_pack_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )
            await db.commit()
        async with factory() as db:
            svc = EvictionService(
                settings=get_settings(), db=db, vector_store=None, graph_engine=None
            )
            n = await svc._evict_expired_context_packs()
            await db.commit()
        assert n == 1
        async with factory() as db:
            row = (
                await db.execute(select(TaskRunDetail).where(TaskRunDetail.run_id == run_id))
            ).scalar_one()
        assert row.context_pack is None  # expired -> NULLed
        assert row.policy_decision == {"decision": "auto_execute"}  # durable -> preserved


async def test_unexpired_pack_kept():
    async with _run_env() as (factory, ws, uid):
        run_id = f"run_{ULID()}"
        async with factory() as db:
            db.add(
                TaskRun(
                    run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="completed"
                )
            )
            db.add(
                TaskRunDetail(
                    run_id=run_id,
                    workspace_id=ws,
                    context_pack={"task_summary": "fresh"},
                    context_pack_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                )
            )
            await db.commit()
        async with factory() as db:
            svc = EvictionService(
                settings=get_settings(), db=db, vector_store=None, graph_engine=None
            )
            n = await svc._evict_expired_context_packs()
            await db.commit()
        assert n == 0
