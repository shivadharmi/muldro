"""RunDetailStore owns the extracted run detail: upsert context_pack (with TTL) +
policy_decision onto the 1:1 row, and get with an expiry render fallback (an expired or
absent context_pack dereferences to None). Step 5, D-C1/D-C2/D-C3."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.task_graph import TaskRun, TaskRunDetail
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


async def _seed_run(factory, ws, uid) -> str:
    run_id = f"run_{ULID()}"
    async with factory() as db:
        db.add(
            TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="pending")
        )
        await db.commit()
    return run_id


async def test_upsert_and_get_context_pack():
    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_run(factory, ws, uid)
        async with factory() as db:
            store = RunDetailStore(db)
            await store.upsert_context_pack(run_id, ws, {"task_summary": "hi", "memories": [1]})
            await db.commit()
        async with factory() as db:
            store = RunDetailStore(db)
            got = await store.get_context_pack(run_id)
        assert got == {"task_summary": "hi", "memories": [1]}


async def test_upsert_policy_then_context_shares_one_row():
    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_run(factory, ws, uid)
        async with factory() as db:
            store = RunDetailStore(db)
            await store.upsert_policy_decision(run_id, ws, {"decision": "auto_execute"})
            await store.upsert_context_pack(run_id, ws, {"task_summary": "x"})
            await db.commit()
        async with factory() as db:
            store = RunDetailStore(db)
            assert await store.get_policy_decision(run_id) == {"decision": "auto_execute"}
            assert await store.get_context_pack(run_id) == {"task_summary": "x"}


async def test_expired_context_pack_dereferences_to_none():
    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_run(factory, ws, uid)
        async with factory() as db:
            db.add(
                TaskRunDetail(
                    run_id=run_id,
                    workspace_id=ws,
                    context_pack={"task_summary": "old"},
                    context_pack_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )
            await db.commit()
        async with factory() as db:
            store = RunDetailStore(db)
            assert await store.get_context_pack(run_id) is None  # expired -> fallback
            # policy_decision on the same expired-pack row is unaffected
            assert await store.get_policy_decision(run_id) is None


async def test_get_absent_run_returns_none():
    async with _run_env() as (factory, ws, uid):
        async with factory() as db:
            store = RunDetailStore(db)
            assert await store.get_context_pack(f"run_{ULID()}") is None
            assert await store.get_policy_decision(f"run_{ULID()}") is None
