import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from ulid import ULID

from src.config.settings import get_settings
from src.models.connection_map import ConnectionMap
from src.services.connection_service import ConnectionService, mint_connection_name
from tests.conftest import TEST_WORKSPACE_ID, make_test_db, seed_user_workspace


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


async def _cleanup(factory, principal_id, alias):
    async with factory() as db:
        await db.execute(
            ConnectionMap.__table__.delete().where(
                ConnectionMap.tenant_id == TEST_WORKSPACE_ID,
                ConnectionMap.principal_id == principal_id,
                ConnectionMap.account_alias == alias,
            )
        )
        await db.commit()


async def test_begin_connection_upserts_pending_and_returns_url():
    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    alias = "work"
    try:
        await seed_user_workspace(factory, pid, TEST_WORKSPACE_ID)
        admin = AsyncMock()
        admin.start_authorization = AsyncMock(
            return_value={"service": "gmail", "authorizationUrl": "https://consent", "state": "s1"}
        )
        svc = ConnectionService(admin_client=admin)
        async with factory() as db:
            url = await svc.begin_connection(
                db, workspace_id=TEST_WORKSPACE_ID, principal_id=pid, provider="gmail", alias=alias
            )
            await db.commit()

        assert url == "https://consent"
        expected_name = mint_connection_name(TEST_WORKSPACE_ID, pid, "gmail", alias)
        admin.start_authorization.assert_awaited_once_with(
            service="gmail", connection_name=expected_name
        )
        async with factory() as db:
            row = (
                await db.execute(
                    select(ConnectionMap).where(
                        ConnectionMap.principal_id == pid, ConnectionMap.account_alias == alias
                    )
                )
            ).scalar_one()
        assert row.connection_status == "pending"
        assert row.connection_id == expected_name
    finally:
        await _cleanup(factory, pid, alias)
        await engine.dispose()


async def test_confirm_connection_flips_pending_to_active_when_configured():
    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    alias = "work"
    try:
        await seed_user_workspace(factory, pid, TEST_WORKSPACE_ID)
        name = mint_connection_name(TEST_WORKSPACE_ID, pid, "gmail", alias)
        async with factory() as db:
            db.add(
                ConnectionMap(
                    tenant_id=TEST_WORKSPACE_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                    principal_id=pid,
                    provider_id="gmail",
                    connection_id=name,
                    connection_status="pending",
                    account_alias=alias,
                )
            )
            await db.commit()

        admin = AsyncMock()
        admin.list_connections = AsyncMock(
            return_value=[{"connectionName": name, "configured": True}]
        )
        svc = ConnectionService(admin_client=admin)
        async with factory() as db:
            active = await svc.confirm_connection(
                db, workspace_id=TEST_WORKSPACE_ID, principal_id=pid, provider="gmail", alias=alias
            )
            await db.commit()

        assert active is True
        async with factory() as db:
            row = (
                await db.execute(select(ConnectionMap).where(ConnectionMap.principal_id == pid))
            ).scalar_one()
        assert row.connection_status == "active"
    finally:
        await _cleanup(factory, pid, alias)
        await engine.dispose()


async def test_confirm_connection_stays_pending_when_not_configured():
    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    alias = "work"
    try:
        await seed_user_workspace(factory, pid, TEST_WORKSPACE_ID)
        name = mint_connection_name(TEST_WORKSPACE_ID, pid, "gmail", alias)
        async with factory() as db:
            db.add(
                ConnectionMap(
                    tenant_id=TEST_WORKSPACE_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                    principal_id=pid,
                    provider_id="gmail",
                    connection_id=name,
                    connection_status="pending",
                    account_alias=alias,
                )
            )
            await db.commit()

        admin = AsyncMock()
        admin.list_connections = AsyncMock(return_value=[])  # not yet consented
        svc = ConnectionService(admin_client=admin)
        async with factory() as db:
            active = await svc.confirm_connection(
                db, workspace_id=TEST_WORKSPACE_ID, principal_id=pid, provider="gmail", alias=alias
            )
            await db.commit()

        assert active is False
        async with factory() as db:
            row = (
                await db.execute(select(ConnectionMap).where(ConnectionMap.principal_id == pid))
            ).scalar_one()
        assert row.connection_status == "pending"
    finally:
        await _cleanup(factory, pid, alias)
        await engine.dispose()


async def test_begin_connection_does_not_demote_active_connection():
    """A stray re-begin on an already-active connection must not demote it."""
    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    alias = "work"
    try:
        await seed_user_workspace(factory, pid, TEST_WORKSPACE_ID)
        name = mint_connection_name(TEST_WORKSPACE_ID, pid, "gmail", alias)
        async with factory() as db:
            db.add(
                ConnectionMap(
                    tenant_id=TEST_WORKSPACE_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                    principal_id=pid,
                    provider_id="gmail",
                    connection_id=name,
                    connection_status="active",
                    account_alias=alias,
                )
            )
            await db.commit()

        admin = AsyncMock()
        admin.start_authorization = AsyncMock(
            return_value={"service": "gmail", "authorizationUrl": "https://consent", "state": "s"}
        )
        svc = ConnectionService(admin_client=admin)
        async with factory() as db:
            url = await svc.begin_connection(
                db, workspace_id=TEST_WORKSPACE_ID, principal_id=pid, provider="gmail", alias=alias
            )
            await db.commit()

        assert url == "https://consent"  # re-auth URL still issued
        async with factory() as db:
            row = (
                await db.execute(select(ConnectionMap).where(ConnectionMap.principal_id == pid))
            ).scalar_one()
        assert row.connection_status == "active"  # NOT demoted to pending
    finally:
        await _cleanup(factory, pid, alias)
        await engine.dispose()


# ---------------------------------------------------------------------------
# Connecting a gateway provider must enable the perception schedules its sources
# feed. The native OAuth callback did this; its only caller was deleted with the
# Google/GitHub OAuth branches, so without this a correctly connected Google
# Workspace never gets observe_gmail/observe_calendar turned on and perception
# never runs at all.
# ---------------------------------------------------------------------------

_SCHED_WS = "ws_conn_svc_schedules"


async def _schedule_names_enabled(factory, workspace_id: str) -> dict[str, bool]:
    from src.models.schedules import Schedule

    async with factory() as db:
        rows = (
            await db.execute(select(Schedule).where(Schedule.workspace_id == workspace_id))
        ).scalars()
        return {s.name: s.enabled for s in rows}


async def _cleanup_schedule_ws(factory, workspace_id: str) -> None:
    from src.models.perception_state import PerceptionState
    from src.models.schedules import Schedule

    async with factory() as db:
        await db.execute(
            ConnectionMap.__table__.delete().where(ConnectionMap.workspace_id == workspace_id)
        )
        await db.execute(
            PerceptionState.__table__.delete().where(PerceptionState.workspace_id == workspace_id)
        )
        await db.execute(Schedule.__table__.delete().where(Schedule.workspace_id == workspace_id))
        await db.commit()


async def _seed_pending_connection(factory, pid: str, provider: str, workspace_id: str) -> None:
    async with factory() as db:
        db.add(
            ConnectionMap(
                tenant_id=workspace_id,
                workspace_id=workspace_id,
                principal_id=pid,
                provider_id=provider,
                connection_id=mint_connection_name(workspace_id, pid, provider, "default"),
                connection_status="pending",
                account_alias="default",
            )
        )
        await db.commit()


def _configured_admin(name: str):
    admin = AsyncMock()
    admin.list_connections = AsyncMock(return_value=[{"connectionName": name, "configured": True}])
    return admin


async def test_confirm_enables_schedules_for_the_providers_perception_sources():
    """googlecalendar -> the "calendar" source, NOT "googlecalendar"."""
    from src.services.schedule_seeder import seed_default_schedules

    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    try:
        await seed_user_workspace(factory, pid, _SCHED_WS)
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        async with factory() as db:
            await seed_default_schedules(db, pid, _SCHED_WS)
            await db.commit()
        assert (await _schedule_names_enabled(factory, _SCHED_WS))["observe_calendar"] is False

        await _seed_pending_connection(factory, pid, "googlecalendar", _SCHED_WS)
        name = mint_connection_name(_SCHED_WS, pid, "googlecalendar", "default")
        svc = ConnectionService(admin_client=_configured_admin(name))
        async with factory() as db:
            active = await svc.confirm_connection(
                db,
                workspace_id=_SCHED_WS,
                principal_id=pid,
                provider="googlecalendar",
                alias="default",
            )
            await db.commit()

        assert active is True
        enabled = await _schedule_names_enabled(factory, _SCHED_WS)
        assert enabled["observe_calendar"] is True
        # Provider-scoped: connecting Calendar must not turn on Gmail polling.
        assert enabled["observe_gmail"] is False
        # ... and the source's PerceptionState is provisioned so the tick sees it.
        from src.models.perception_state import PerceptionState

        async with factory() as db:
            state = (
                await db.execute(
                    select(PerceptionState).where(PerceptionState.workspace_id == _SCHED_WS)
                )
            ).scalar_one()
        assert state.source == "calendar"
        assert state.mode == "poll"
    finally:
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        await engine.dispose()


async def test_confirm_enables_the_github_schedule_from_its_declared_source():
    """github declares perception_sources=("github",), so confirming it enables that schedule.

    It was previously declared empty, which routed the "github" perception source
    through the retired-OAuth branch where ``no_token`` is a PERMANENT reauth
    reason -- pausing the row unrecoverably. This asserts the declaration is wired
    all the way through, not just present in the registry.
    """
    from src.services.schedule_seeder import seed_default_schedules

    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    try:
        await seed_user_workspace(factory, pid, _SCHED_WS)
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        async with factory() as db:
            await seed_default_schedules(db, pid, _SCHED_WS)
            await db.commit()

        await _seed_pending_connection(factory, pid, "github", _SCHED_WS)
        name = mint_connection_name(_SCHED_WS, pid, "github", "default")
        svc = ConnectionService(admin_client=_configured_admin(name))
        async with factory() as db:
            active = await svc.confirm_connection(
                db, workspace_id=_SCHED_WS, principal_id=pid, provider="github", alias="default"
            )
            await db.commit()

        assert active is True
        enabled = await _schedule_names_enabled(factory, _SCHED_WS)
        assert enabled.get("observe_github") is True
        # Confirming github must not enable another provider's source.
        assert not enabled.get("observe_gmail")
        assert not enabled.get("observe_calendar")
    finally:
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        await engine.dispose()


async def test_confirm_enables_nothing_for_a_provider_the_registry_does_not_know():
    """Fail-closed: an unregistered provider activates but enables no schedule.

    Every registered provider now declares a perception source, so the
    "no source" case is only reachable off-registry -- and it must not fall back
    to enabling something.
    """
    from src.services.schedule_seeder import seed_default_schedules

    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    try:
        await seed_user_workspace(factory, pid, _SCHED_WS)
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        async with factory() as db:
            await seed_default_schedules(db, pid, _SCHED_WS)
            await db.commit()

        await _seed_pending_connection(factory, pid, "dropbox", _SCHED_WS)
        name = mint_connection_name(_SCHED_WS, pid, "dropbox", "default")
        svc = ConnectionService(admin_client=_configured_admin(name))
        async with factory() as db:
            active = await svc.confirm_connection(
                db, workspace_id=_SCHED_WS, principal_id=pid, provider="dropbox", alias="default"
            )
            await db.commit()

        assert active is True  # the connection still activates
        # No observe_* schedule was touched (the connector-independent globals are
        # seeded enabled at workspace creation and are not this call's doing).
        enabled = await _schedule_names_enabled(factory, _SCHED_WS)
        assert not any(v for k, v in enabled.items() if k.startswith("observe_"))
    finally:
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        await engine.dispose()


async def test_repeat_confirm_does_not_re_enable_a_disabled_schedule():
    """Confirm is POLLED. Only the pending -> active edge may touch schedules."""
    from src.models.schedules import Schedule
    from src.services.schedule_seeder import seed_default_schedules

    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    try:
        await seed_user_workspace(factory, pid, _SCHED_WS)
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        async with factory() as db:
            await seed_default_schedules(db, pid, _SCHED_WS)
            await db.commit()

        await _seed_pending_connection(factory, pid, "gmail", _SCHED_WS)
        name = mint_connection_name(_SCHED_WS, pid, "gmail", "default")
        svc = ConnectionService(admin_client=_configured_admin(name))
        async with factory() as db:
            await svc.confirm_connection(
                db, workspace_id=_SCHED_WS, principal_id=pid, provider="gmail", alias="default"
            )
            await db.commit()
        assert (await _schedule_names_enabled(factory, _SCHED_WS))["observe_gmail"] is True

        # User turns it back off, then the UI polls confirm again.
        async with factory() as db:
            sched = (
                await db.execute(
                    select(Schedule).where(
                        Schedule.workspace_id == _SCHED_WS, Schedule.name == "observe_gmail"
                    )
                )
            ).scalar_one()
            sched.enabled = False
            await db.commit()

        async with factory() as db:
            active = await svc.confirm_connection(
                db, workspace_id=_SCHED_WS, principal_id=pid, provider="gmail", alias="default"
            )
            await db.commit()

        assert active is True
        assert (await _schedule_names_enabled(factory, _SCHED_WS))["observe_gmail"] is False
    finally:
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        await engine.dispose()


async def test_schedule_seeding_failure_never_fails_the_connection():
    """A SAVEPOINT keeps a seeding fault from poisoning the caller's transaction."""
    from unittest.mock import patch

    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    try:
        await seed_user_workspace(factory, pid, _SCHED_WS)
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        await _seed_pending_connection(factory, pid, "gmail", _SCHED_WS)
        name = mint_connection_name(_SCHED_WS, pid, "gmail", "default")
        svc = ConnectionService(admin_client=_configured_admin(name))

        async def _boom(*args, **kwargs):
            raise RuntimeError("seeder exploded")

        with patch("src.services.schedule_seeder.enable_schedules_for_connector", _boom):
            async with factory() as db:
                active = await svc.confirm_connection(
                    db, workspace_id=_SCHED_WS, principal_id=pid, provider="gmail", alias="default"
                )
                await db.commit()

        assert active is True
        async with factory() as db:
            row = (
                await db.execute(select(ConnectionMap).where(ConnectionMap.principal_id == pid))
            ).scalar_one()
        assert row.connection_status == "active"  # activation survived
    finally:
        await _cleanup_schedule_ws(factory, _SCHED_WS)
        await engine.dispose()
