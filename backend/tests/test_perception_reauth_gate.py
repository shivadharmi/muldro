"""Tests for the validity-aware OAuth re-auth gate in the perception tick.

The perception tick's pre-flight gate must distinguish a *permanently* unusable
credential (no_token / no_refresh_token / revoked) from a *transient* refresh
blip (refresh_failed) and from a healthy token (ok):

- ``ok``             → source stays runnable.
- PERMANENT reasons  → source dropped AND ``ReauthService.mark_needs_reauth`` is
  called once per (user, provider) for the tick (pauses + notifies, deduped).
- ``refresh_failed`` → source stays runnable (let the normal poll/circuit-breaker
  flow handle a genuine outage; do not pause or notify).

The OAuthManager validity check is made ONCE per (user, provider) per tick.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.perception_state import PerceptionState
from src.services.oauth_manager import TokenResult


def _make_state(**overrides) -> PerceptionState:
    defaults = dict(
        state_id="pst_test",
        workspace_id="ws_test",
        user_id="usr_test",
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


def _token_result(reason: str) -> TokenResult:
    return TokenResult(token="tok" if reason == "ok" else None, reason=reason)


@pytest.fixture
def scheduler():
    from src.services.scheduler import SchedulerLoop
    from tests.conftest import make_mock_settings

    orchestrator = MagicMock()
    return SchedulerLoop(make_mock_settings(), orchestrator=orchestrator)


# ---------------------------------------------------------------------------
# (a) valid token keeps the source runnable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_token_keeps_source_runnable(scheduler):
    state = _make_state(source="gmail")

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("ok"))
    reauth = MagicMock()
    reauth.mark_needs_reauth = AsyncMock()

    db = MagicMock()
    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        kept = await scheduler._drop_tokenless_sources(db, [state])

    assert kept == [state]
    assert state.mode != "paused"
    reauth.mark_needs_reauth.assert_not_called()
    oauth.get_valid_token_with_reason.assert_awaited_once_with("usr_test", "google")


# ---------------------------------------------------------------------------
# (b) permanent reasons drop the source AND mark_needs_reauth once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["no_token", "no_refresh_token", "revoked"])
async def test_permanent_reason_drops_and_applies_reauth_on_outer_db(scheduler, reason):
    # The gate must apply the needs-reauth writes on the CALLER's session
    # (apply_needs_reauth(db, ...)) — NOT open a second session via
    # mark_needs_reauth — to avoid a cross-session row-lock self-deadlock with
    # the tick's FOR UPDATE locks on the same perception_state rows.
    state = _make_state(source="gmail", user_id="usr_x", workspace_id="ws_x")

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result(reason))
    reauth = MagicMock()
    reauth.apply_needs_reauth = AsyncMock()
    reauth.mark_needs_reauth = AsyncMock()

    db = MagicMock()
    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        kept = await scheduler._drop_tokenless_sources(db, [state])

    assert kept == []  # dropped from runnable list
    # Writes happen on the passed-in db, not a fresh session.
    reauth.apply_needs_reauth.assert_awaited_once()
    args, kwargs = reauth.apply_needs_reauth.await_args
    # signature: apply_needs_reauth(db, user_id, provider, reason)
    assert args[0] is db
    assert args[1] == "usr_x"
    assert args[2] == "google"
    assert args[3] == reason
    # mark_needs_reauth (which opens its own session) must NOT be used here.
    reauth.mark_needs_reauth.assert_not_called()


@pytest.mark.asyncio
async def test_gate_returns_collected_reauth_tuples_for_post_commit_notify(scheduler):
    # The gate collects (user_id, provider, reason, workspace_id) tuples so the
    # tick can notify AFTER its commit (notify is Redis + external; must be
    # post-commit). Verify the gate exposes them via _drop_tokenless_sources
    # returning (kept, marked_tuples) OR sets them on a known attribute.
    state = _make_state(source="gmail", user_id="usr_x", workspace_id="ws_x")

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("revoked"))
    reauth = MagicMock()
    reauth.apply_needs_reauth = AsyncMock()

    collected: list = []

    db = MagicMock()
    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        kept = await scheduler._drop_tokenless_sources(db, [state], marked_out=collected)

    assert kept == []
    assert collected == [("usr_x", "google", "revoked", "ws_x")]


# ---------------------------------------------------------------------------
# (c) gmail + calendar (same google provider) → ONE mark_needs_reauth call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_and_calendar_collapse_to_one_reauth(scheduler):
    s_gmail = _make_state(state_id="pst_g", source="gmail", user_id="usr_g")
    s_cal = _make_state(state_id="pst_c", source="calendar", user_id="usr_g")

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("revoked"))
    reauth = MagicMock()
    reauth.apply_needs_reauth = AsyncMock()

    db = MagicMock()
    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        kept = await scheduler._drop_tokenless_sources(db, [s_gmail, s_cal])

    assert kept == []
    # Both sources map to the google provider → ONE validity check, ONE reauth.
    oauth.get_valid_token_with_reason.assert_awaited_once_with("usr_g", "google")
    reauth.apply_needs_reauth.assert_awaited_once()


# ---------------------------------------------------------------------------
# (d) refresh_failed (transient) keeps the source — no pause, no notify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_failed_keeps_source_no_notify(scheduler):
    state = _make_state(source="gmail")

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("refresh_failed"))
    reauth = MagicMock()
    reauth.mark_needs_reauth = AsyncMock()

    db = MagicMock()
    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        kept = await scheduler._drop_tokenless_sources(db, [state])

    assert kept == [state]  # transient → keep
    assert state.mode != "paused"
    reauth.mark_needs_reauth.assert_not_called()


# ---------------------------------------------------------------------------
# Caching: two sources, same (user, provider) → ONE validity check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validity_check_cached_per_user_provider(scheduler):
    s_gmail = _make_state(state_id="pst_g", source="gmail", user_id="usr_g")
    s_cal = _make_state(state_id="pst_c", source="calendar", user_id="usr_g")

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("ok"))
    reauth = MagicMock()

    db = MagicMock()
    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        kept = await scheduler._drop_tokenless_sources(db, [s_gmail, s_cal])

    assert kept == [s_gmail, s_cal]
    # Same provider for both → cached → one underlying call.
    oauth.get_valid_token_with_reason.assert_awaited_once()


# ---------------------------------------------------------------------------
# Safety: an unexpected exception in the gate keeps all sources (fail-open)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_failure_keeps_all_sources(scheduler):
    state = _make_state(source="gmail")

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(side_effect=RuntimeError("boom"))
    reauth = MagicMock()
    reauth.mark_needs_reauth = AsyncMock()

    db = MagicMock()
    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        kept = await scheduler._drop_tokenless_sources(db, [state])

    assert kept == [state]  # fail-open: keep rather than nuke the tick
    assert state.mode != "paused"


# ---------------------------------------------------------------------------
# No OAuthManager available → degrade gracefully (keep all, no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_oauth_manager_keeps_all_sources(scheduler):
    state = _make_state(source="gmail")

    db = MagicMock()
    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(None, None)):
        kept = await scheduler._drop_tokenless_sources(db, [state])

    assert kept == [state]
    assert state.mode != "paused"


# ---------------------------------------------------------------------------
# Self-healing reaper: a source paused-for-reauth whose token is valid again
# gets cleared (clear_reauth) so the callback-missed case still recovers.
# ---------------------------------------------------------------------------


def _factory_for(db):
    def factory():
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    return factory


@pytest.mark.asyncio
async def test_reauth_recovery_clears_when_token_valid_again(scheduler):
    # One source paused-for-reauth (mode=paused, last_error=needs_reauth).
    paused = _make_state(state_id="pst_p", source="gmail", user_id="usr_p", mode="paused")
    paused.last_error = "needs_reauth"

    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [paused]
    db = MagicMock()
    db.execute = AsyncMock(return_value=rows)
    db.commit = AsyncMock()

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("ok"))
    reauth = MagicMock()
    reauth.clear_reauth = AsyncMock()

    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        await scheduler._tick_reauth_recovery(_factory_for(db))

    reauth.clear_reauth.assert_awaited_once()
    args, kwargs = reauth.clear_reauth.await_args
    assert args[0] == "usr_p"
    assert args[1] == "google"


@pytest.mark.asyncio
async def test_reauth_recovery_noop_when_still_invalid(scheduler):
    paused = _make_state(state_id="pst_p", source="gmail", user_id="usr_p", mode="paused")
    paused.last_error = "needs_reauth"

    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [paused]
    db = MagicMock()
    db.execute = AsyncMock(return_value=rows)
    db.commit = AsyncMock()

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("revoked"))
    reauth = MagicMock()
    reauth.clear_reauth = AsyncMock()

    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        await scheduler._tick_reauth_recovery(_factory_for(db))

    reauth.clear_reauth.assert_not_called()


# ---------------------------------------------------------------------------
# Reaper second branch: providers with NO perception source (notion/atlassian).
# A deferred TaskRun on such a provider must still recover if the OAuth-callback
# resume was missed — keyed off awaiting_reauth TaskRuns, not perception_state.
# ---------------------------------------------------------------------------


def _make_awaiting_run(user_id, provider, ws_id="ws_p"):
    run = MagicMock()
    run.run_id = "run_p"
    run.user_id = user_id
    run.workspace_id = ws_id
    run.status = "awaiting_reauth"
    run.checkpoint = {"awaiting_provider": provider}
    return run


@pytest.mark.asyncio
async def test_reauth_recovery_clears_taskrun_provider_without_perception_source(scheduler):
    # No paused perception_state rows (notion has no perception source), but a
    # deferred TaskRun awaiting notion re-auth exists and the token is valid.
    run = _make_awaiting_run("usr_n", "notion")

    pstate_rows = MagicMock()
    pstate_rows.scalars.return_value.all.return_value = []  # no paused sources
    run_rows = MagicMock()
    run_rows.scalars.return_value.all.return_value = [run]

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[pstate_rows, run_rows])
    db.commit = AsyncMock()

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("ok"))
    reauth = MagicMock()
    reauth.clear_reauth = AsyncMock()

    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        await scheduler._tick_reauth_recovery(_factory_for(db))

    reauth.clear_reauth.assert_awaited_once()
    args, _ = reauth.clear_reauth.await_args
    assert args[0] == "usr_n"
    assert args[1] == "notion"


@pytest.mark.asyncio
async def test_reauth_recovery_taskrun_branch_noop_when_invalid(scheduler):
    run = _make_awaiting_run("usr_n", "atlassian")

    pstate_rows = MagicMock()
    pstate_rows.scalars.return_value.all.return_value = []
    run_rows = MagicMock()
    run_rows.scalars.return_value.all.return_value = [run]

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[pstate_rows, run_rows])
    db.commit = AsyncMock()

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("revoked"))
    reauth = MagicMock()
    reauth.clear_reauth = AsyncMock()

    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        await scheduler._tick_reauth_recovery(_factory_for(db))

    reauth.clear_reauth.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_recovery_dedupes_validity_check_across_branches(scheduler):
    # A google provider appears in BOTH a paused perception_state AND a deferred
    # TaskRun. The validity check must run ONCE per (user, provider) and clear
    # ONCE — not twice.
    paused = _make_state(state_id="pst_p", source="gmail", user_id="usr_p", mode="paused")
    paused.last_error = "needs_reauth"
    run = _make_awaiting_run("usr_p", "google")

    pstate_rows = MagicMock()
    pstate_rows.scalars.return_value.all.return_value = [paused]
    run_rows = MagicMock()
    run_rows.scalars.return_value.all.return_value = [run]

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[pstate_rows, run_rows])
    db.commit = AsyncMock()

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(return_value=_token_result("ok"))
    reauth = MagicMock()
    reauth.clear_reauth = AsyncMock()

    with patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)):
        await scheduler._tick_reauth_recovery(_factory_for(db))

    oauth.get_valid_token_with_reason.assert_awaited_once_with("usr_p", "google")
    reauth.clear_reauth.assert_awaited_once()
