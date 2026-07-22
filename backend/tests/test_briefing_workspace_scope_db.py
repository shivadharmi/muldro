"""Real-DB regression: today's-briefing lookup must be workspace-scoped.

Briefing is workspace-scoped, but the per-day lookup used by generate_briefing
(idempotency check + delivery fetch) historically filtered only on
(user_id, briefing_date). For a user with two workspaces that both have a
briefing today, the un-scoped query's `ORDER BY created_at DESC LIMIT 1` returns
whichever row is newest regardless of workspace — so one workspace's briefing
could satisfy the other's idempotency check (suppressing it) or be pushed to the
wrong surface. _get_todays_briefing must return the row for the QUERIED
workspace.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.briefings import Briefing
from src.models.users import User, Workspace
from src.orchestrator.jarvis import JarvisOrchestrator
from src.orchestrator.services import ServiceContainer
from src.services.presenter import Presenter
from tests.conftest import make_mock_settings


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
async def _env():
    """One user, two workspaces, each with today's briefing.

    ws_a's briefing is written NEWER than ws_b's so an un-scoped
    `ORDER BY created_at DESC LIMIT 1` would wrongly return ws_a for either
    workspace — the scoped lookup must ignore that and return the right row.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws_a, ws_b = f"ws_a_{suffix}", f"ws_b_{suffix}"
    brief_a, brief_b = f"brief_a_{suffix}", f"brief_b_{suffix}"
    now = datetime.now(timezone.utc)
    try:
        async with factory() as db:
            db.add(User(user_id=uid, email=f"brief-{suffix}@example.com", display_name="brief"))
            db.add(Workspace(workspace_id=ws_a, name="ws-a", owner_user_id=uid))
            db.add(Workspace(workspace_id=ws_b, name="ws-b", owner_user_id=uid))
            await db.commit()
        async with factory() as db:
            # ws_b first (older), ws_a second (newer) — explicit created_at so the
            # newest-row-wins bug is deterministic, not dependent on insert timing.
            db.add(
                Briefing(
                    briefing_id=brief_b,
                    user_id=uid,
                    workspace_id=ws_b,
                    briefing_date=date.today(),
                    created_at=now - timedelta(minutes=1),
                )
            )
            db.add(
                Briefing(
                    briefing_id=brief_a,
                    user_id=uid,
                    workspace_id=ws_a,
                    briefing_date=date.today(),
                    created_at=now,
                )
            )
            await db.commit()
        yield factory, uid, ws_a, ws_b, brief_a, brief_b
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Briefing).where(Briefing.user_id == uid))
                await db.execute(delete(Workspace).where(Workspace.owner_user_id == uid))
                await db.execute(delete(User).where(User.user_id == uid))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


def _orchestrator(factory):
    return JarvisOrchestrator(
        settings=make_mock_settings(),
        db_factory=factory,
        services=ServiceContainer(),
    )


async def test_get_todays_briefing_returns_queried_workspace_row():
    async with _env() as (factory, uid, ws_a, ws_b, brief_a, brief_b):
        orch = _orchestrator(factory)

        row_b = await orch._get_todays_briefing(uid, ws_b)
        assert row_b is not None
        assert row_b.briefing_id == brief_b
        assert row_b.workspace_id == ws_b

        row_a = await orch._get_todays_briefing(uid, ws_a)
        assert row_a is not None
        assert row_a.briefing_id == brief_a
        assert row_a.workspace_id == ws_a


async def test_briefing_already_exists_is_workspace_scoped():
    """A workspace with no briefing today must report False even when the SAME
    user has a briefing in a DIFFERENT workspace."""
    async with _env() as (factory, uid, ws_a, ws_b, brief_a, brief_b):
        orch = _orchestrator(factory)
        # Both workspaces have a briefing → both True.
        assert await orch._briefing_already_exists(uid, ws_a) is True
        assert await orch._briefing_already_exists(uid, ws_b) is True

        # A third workspace with no briefing today → False (not shadowed by ws_a/ws_b).
        empty_ws = f"ws_empty_{ULID()}"
        assert await orch._briefing_already_exists(uid, empty_ws) is False


async def test_presenter_generate_briefing_cache_is_workspace_scoped():
    """Presenter.generate_briefing's cache-check must scope workspace_id.

    A user with a briefing in TWO workspaces today matches both rows on the
    unscoped (user_id, briefing_date) filter, so scalar_one_or_none() raises
    MultipleResultsFound — and even with a single match it could hand back a
    different workspace's cached briefing. The scoped check must return the
    queried workspace's row (here ws_a's briefing, not ws_b's).
    """
    async with _env() as (factory, uid, ws_a, ws_b, brief_a, brief_b):
        async with factory() as db:
            presenter = Presenter(settings=make_mock_settings(), db=db)
            cached = await presenter.generate_briefing(uid, date.today(), workspace_id=ws_a)
        assert cached.briefing_id == brief_a
        assert cached.workspace_id == ws_a
