"""The perception runnability gate for GATEWAY-backed sources.

Gmail and Calendar credentials live inside OpenConnector, recorded locally as
`connection_map` rows. There is no OAuthManager token for them and no route that
can mint one, so asking OAuthManager whether they are runnable answers
``no_token`` forever — a PERMANENT re-auth reason, which dropped both sources and
marked them ``needs_reauth`` unrecoverably. Gmail and Calendar perception stopped
entirely, and nothing raised.

These tests pin the corrected rule (the same one
``integration_status.active_connection_providers`` already applies): a gateway
source is runnable when THIS principal has an ACTIVE connection under the default
alias, and OAuthManager is never consulted. The connection_map side runs against
a real Postgres (same ``_db_reachable`` skip-guard idiom as
``tests/test_integration_status_gateway.py``) so the scoping is exercised for
real rather than mocked into agreement.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import get_settings
from src.models.connection_map import DEFAULT_ACCOUNT_ALIAS, ConnectionMap
from src.models.perception_state import PerceptionState
from tests.conftest import make_test_db, seed_user_workspace

# Dedicated workspace so rows seeded by other real-DB tests cannot leak in.
_WS = "ws_perception_gateway_gate"
_USER = "usr_01JTESTPERCEPTIONGATEWAY000"


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


def _make_state(**overrides) -> PerceptionState:
    defaults = dict(
        state_id="pst_gw",
        workspace_id=_WS,
        user_id=_USER,
        source="gmail",
        mode="poll",
        base_interval_s=300,
        effective_interval_s=300,
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        last_run_at=None,
        agent_interval_s=None,
        watch_entities=None,
        consecutive_failures=0,
        last_error=None,
        circuit_state="closed",
        circuit_opened_at=None,
        pending_run=True,
        signal_source=None,
        signal_at=None,
        last_event_count=0,
        total_runs=0,
    )
    defaults.update(overrides)
    return PerceptionState(**defaults)


@pytest.fixture
def scheduler():
    from src.services.scheduler import SchedulerLoop
    from tests.conftest import make_mock_settings

    return SchedulerLoop(make_mock_settings(), orchestrator=MagicMock())


async def _clear_connections(factory) -> None:
    async with factory() as db:
        await db.execute(ConnectionMap.__table__.delete().where(ConnectionMap.workspace_id == _WS))
        await db.commit()


async def _add_connection(
    factory,
    provider_id: str,
    status: str = "active",
    *,
    principal_id: str = _USER,
    alias: str = DEFAULT_ACCOUNT_ALIAS,
) -> None:
    async with factory() as db:
        db.add(
            ConnectionMap(
                tenant_id=_WS,
                workspace_id=_WS,
                principal_id=principal_id,
                provider_id=provider_id,
                connection_id=f"{_WS}:{principal_id}:{provider_id}:{alias}",
                connection_status=status,
                account_alias=alias,
            )
        )
        await db.commit()


# ---------------------------------------------------------------------------
# The regression: an ACTIVE connection keeps the source runnable even though no
# OAuthManager exists at all.
# ---------------------------------------------------------------------------


async def test_connected_gateway_sources_run_without_any_oauth_manager(scheduler):
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")
        await _add_connection(factory, "googlecalendar")

        s_gmail = _make_state(state_id="pst_g", source="gmail")
        s_cal = _make_state(state_id="pst_c", source="calendar")

        # (None, None) == "no OAuthManager available at all" — the behavioural
        # assertion that the gateway branch never needs a token, rather than
        # patching the gate's own source lookup.
        marked: list = []
        with patch.object(scheduler, "_validity_gate_collaborators", return_value=(None, None)):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(
                    db, [s_gmail, s_cal], marked_out=marked
                )

        assert kept == [s_gmail, s_cal]
        assert marked == []
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_gateway_source_never_consults_oauth_manager(scheduler):
    """Even when an OAuthManager IS available, the gateway branch must not use it.

    A google token can never exist, so any consultation re-introduces the
    permanent ``no_token`` verdict this fix removed.
    """
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")

        oauth = MagicMock()
        oauth.get_valid_token_with_reason = AsyncMock()
        reauth = MagicMock()
        reauth.apply_needs_reauth = AsyncMock()

        state = _make_state(source="gmail")
        with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(db, [state])

        assert kept == [state]
        oauth.get_valid_token_with_reason.assert_not_called()
        reauth.apply_needs_reauth.assert_not_called()
    finally:
        await _clear_connections(factory)
        await engine.dispose()


# ---------------------------------------------------------------------------
# Not connected yet == skip, NOT a permanent needs_reauth mark.
# ---------------------------------------------------------------------------


async def test_unconnected_gateway_source_is_skipped_not_marked_needs_reauth(scheduler):
    """Marking "never connected" as needs_reauth is the bug, not the fix.

    ``needs_reauth`` pauses the row (so it stops being due) and recovery runs
    through ``OAuthManager`` — which has nothing to recover for a gateway
    provider. The source would be stranded exactly as before. Skipping instead
    leaves the row due, so the tick re-checks it and it starts running the moment
    the user finishes connecting.
    """
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)  # nothing connected

        oauth = MagicMock()
        oauth.get_valid_token_with_reason = AsyncMock()
        reauth = MagicMock()
        reauth.apply_needs_reauth = AsyncMock()
        reauth.mark_needs_reauth = AsyncMock()

        state = _make_state(source="gmail")
        marked: list = []
        with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(db, [state], marked_out=marked)

        assert kept == []  # not polled — there is no credential to poll with
        assert marked == []  # ... and no reconnect prompt was queued
        reauth.apply_needs_reauth.assert_not_called()
        reauth.mark_needs_reauth.assert_not_called()
        assert state.mode == "poll"  # NOT paused — stays due, self-heals on connect
        assert state.last_error != "needs_reauth"
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_pending_connection_is_not_runnable(scheduler):
    """Only ``active`` counts — the adapter's resolver denies anything else."""
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail", status="pending")

        state = _make_state(source="gmail")
        with patch.object(scheduler, "_validity_gate_collaborators", return_value=(None, None)):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(db, [state])

        assert kept == []
        assert state.mode == "poll"
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_another_members_connection_does_not_make_this_source_runnable(scheduler):
    """Runnable must mean the resolver will resolve it FOR THIS principal."""
    factory, engine = make_test_db()
    other = "usr_01JTESTPERCEPTIONOTHERMEMBER"
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail", principal_id=other)

        state = _make_state(source="gmail")
        with patch.object(scheduler, "_validity_gate_collaborators", return_value=(None, None)):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(db, [state])

        assert kept == []
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_partial_connection_runs_only_the_connected_source(scheduler):
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")  # calendar never linked

        s_gmail = _make_state(state_id="pst_g", source="gmail")
        s_cal = _make_state(state_id="pst_c", source="calendar")
        with patch.object(scheduler, "_validity_gate_collaborators", return_value=(None, None)):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(db, [s_gmail, s_cal])

        assert kept == [s_gmail]
    finally:
        await _clear_connections(factory)
        await engine.dispose()


# ---------------------------------------------------------------------------
# Batching: the OAuth branch caches one verdict per (user, provider) per tick;
# the gateway branch must not regress that into one query per source.
# ---------------------------------------------------------------------------


async def test_gateway_lookup_is_batched_per_principal_not_per_source(scheduler):
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")
        await _add_connection(factory, "googlecalendar")

        real = None
        calls: list[tuple] = []

        from src.services import integration_status

        real = integration_status.active_connection_providers

        async def _counting(db, workspace_id, user_id, providers):
            calls.append((workspace_id, user_id, providers))
            return await real(db, workspace_id, user_id, providers)

        states = [
            _make_state(state_id="pst_g", source="gmail"),
            _make_state(state_id="pst_c", source="calendar"),
        ]
        with (
            patch.object(scheduler, "_validity_gate_collaborators", return_value=(None, None)),
            patch.object(integration_status, "active_connection_providers", _counting),
        ):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(db, states)

        assert kept == states
        # Two sources, one principal -> ONE lookup covering both providers.
        assert len(calls) == 1
        assert set(calls[0][2]) == {"gmail", "googlecalendar"}
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_lookup_failure_keeps_gateway_sources(scheduler):
    """Fail-open, matching the OAuth branch: a DB hiccup must not pause a source."""
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)

        from src.services import integration_status

        async def _boom(db, workspace_id, user_id, providers):
            raise RuntimeError("boom")

        state = _make_state(source="gmail")
        with (
            patch.object(scheduler, "_validity_gate_collaborators", return_value=(None, None)),
            patch.object(integration_status, "active_connection_providers", _boom),
        ):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(db, [state])

        assert kept == [state]
    finally:
        await engine.dispose()


async def test_mixed_tick_routes_each_source_to_its_own_branch(scheduler):
    """A gateway source and an OAuth source in one tick keep their own rules."""
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")

        from src.services.oauth_manager import TokenResult

        oauth = MagicMock()
        oauth.get_valid_token_with_reason = AsyncMock(
            return_value=TokenResult(token=None, reason="revoked")
        )
        reauth = MagicMock()
        reauth.apply_needs_reauth = AsyncMock()

        s_gmail = _make_state(state_id="pst_g", source="gmail")
        s_slack = _make_state(state_id="pst_s", source="slack")
        marked: list = []
        with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
            async with factory() as db:
                kept = await scheduler._drop_tokenless_sources(
                    db, [s_gmail, s_slack], marked_out=marked
                )

        assert kept == [s_gmail]  # gateway: connected -> runs
        # OAuth source: revoked -> dropped AND surfaced for re-auth, unchanged.
        oauth.get_valid_token_with_reason.assert_awaited_once_with(_USER, "slack")
        reauth.apply_needs_reauth.assert_awaited_once()
        assert marked == [(_USER, "slack", "revoked", _WS)]
    finally:
        await _clear_connections(factory)
        await engine.dispose()
