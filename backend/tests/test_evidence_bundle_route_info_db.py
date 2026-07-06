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
        db.add(
            TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="pending")
        )
        await db.commit()
    return run_id


async def test_build_for_run_resolves_route_info_from_store():
    """build_for_run threads the persisted policy_decision into EvidenceBundle.route_info."""
    async with _run_env() as (factory, ws, uid):
        run_id = await _seed_run(factory, ws, uid)
        async with factory() as db:
            await RunDetailStore(db).upsert_policy_decision(
                run_id, ws, {"decision": "auto_execute"}
            )
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
