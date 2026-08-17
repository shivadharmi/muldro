"""Tests for claim-and-release lock scoping in the perception scheduler tick.

The perception tick must NOT hold ``perception_state`` row locks across the long
LLM/MCP ``run_perception_cycle`` calls. Instead it CLAIMS due rows in one short
transaction (FOR UPDATE SKIP LOCKED → lease via pending_run=False + next_run_at
advanced by LEASE_TTL_S → commit), then runs cycles UNLOCKED and records each
outcome in its own fresh transaction.

These tests assert that property structurally (the claim tx commits before any
cycle is awaited), that the lease prevents re-pick, that a crash mid-cycle leaves
the source due-again-after-lease, and that outcome recording still works.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.perception_state import PerceptionState
from src.services.perception_policy import (
    LEASE_TTL_S,
    PerceptionPolicyService,
)
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# These tests are about lock/transaction scoping, not credential resolution, so
# they use OAUTH-backed sources (slack/github) throughout. A gateway-backed source
# (gmail/calendar) would take the connection_map branch of the runnability gate and
# be skipped for want of an active connection, emptying the tick under test.
def _make_state(**overrides) -> PerceptionState:
    defaults = dict(
        state_id="pst_test",
        workspace_id="ws_test",
        user_id="usr_test",
        source="slack",
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


def _mock_settings():
    s = MagicMock()
    s.max_perception_per_tick = 5
    s.perception_concurrency = 1
    return s


def _mock_budget():
    budget = MagicMock()
    budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
    budget.should_allow_perception = MagicMock(return_value=True)
    budget.get_perception_interval_multiplier = MagicMock(return_value=1)
    return budget


class _TickHarness:
    """Builds the factory/db/svc plumbing for _tick_perception in claim+record form.

    Phase 1 uses one db (the "claim" db). Phase 2 records each outcome in a fresh
    db per source. We hand out the same recording db for simplicity; what matters
    is that ``commit`` is observable and ordering can be asserted via a shared
    event log.
    """

    def __init__(self, states):
        self.states = states
        self.events: list[str] = []  # ordered log of claim-commit / cycle / record

        # Phase 1 claim db
        self.claim_db = MagicMock()
        self.claim_db.flush = AsyncMock()

        async def _claim_commit():
            self.events.append("claim_commit")

        self.claim_db.commit = AsyncMock(side_effect=_claim_commit)
        self.claim_db.__aenter__ = AsyncMock(return_value=self.claim_db)
        self.claim_db.__aexit__ = AsyncMock(return_value=False)

        # The validity gate (_drop_tokenless_sources) no longer queries the DB —
        # it calls OAuthManager via _validity_gate_collaborators. With a MagicMock
        # orchestrator (and no collaborator patch) the gate fail-opens and keeps
        # every source, which is what these lock-scoping tests want. claim_db.execute
        # remains stubbed only for any incidental ORM call during the claim phase.
        self.claim_db.execute = AsyncMock(return_value=MagicMock())

        # Phase 2 recording db(s) — one fresh context per source
        self.rec_dbs: list[MagicMock] = []

        def _make_factory():
            calls = {"n": 0}

            def factory():
                calls["n"] += 1
                if calls["n"] == 1:
                    # First factory() call is the budget pre-check.
                    budget_db = MagicMock()
                    budget_db.flush = AsyncMock()
                    budget_db.commit = AsyncMock()
                    budget_db.__aenter__ = AsyncMock(return_value=budget_db)
                    budget_db.__aexit__ = AsyncMock(return_value=False)
                    return budget_db
                if calls["n"] == 2:
                    # Second factory() call is the CLAIM transaction.
                    return self.claim_db
                rec_db = MagicMock()
                rec_db.flush = AsyncMock()

                async def _rec_commit():
                    self.events.append("record_commit")

                rec_db.commit = AsyncMock(side_effect=_rec_commit)
                rec_db.__aenter__ = AsyncMock(return_value=rec_db)
                rec_db.__aexit__ = AsyncMock(return_value=False)
                self.rec_dbs.append(rec_db)
                return rec_db

            return factory

        self.factory = _make_factory()

        # Service: claim phase returns ClaimedSource snapshots; recording phase
        # re-fetches by id and records.
        self.svc = MagicMock()
        self.svc.get_due_sources_all_users = AsyncMock(return_value=list(states))

        async def _claim(sources, now=None):
            return await PerceptionPolicyService.claim_due_sources(self.svc, sources, now)

        # bind the real claim implementation against a stub db
        self.svc._db = self.claim_db
        self.svc.claim_due_sources = AsyncMock(side_effect=_claim)

        by_id = {s.state_id: s for s in states}
        self.svc.get_by_state_id = AsyncMock(side_effect=lambda sid: by_id.get(sid))
        self.svc.record_success = AsyncMock()
        self.svc.record_failure = AsyncMock()


# ---------------------------------------------------------------------------
# 1. Lock released during cycle (core property): claim commits before cycle runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_commits_before_cycle_runs():
    """Phase 1 (CLAIM) must commit — releasing the FOR UPDATE lock — BEFORE any
    run_perception_cycle is awaited. Proven via a shared ordered event log."""
    from src.services.scheduler import SchedulerLoop

    state = _make_state()
    harness = _TickHarness([state])

    orchestrator = MagicMock()
    orchestrator._budget = _mock_budget()

    async def _cycle(source, *, user_id, workspace_id):
        # When this runs, the claim transaction must already be committed.
        harness.events.append("cycle")
        assert "claim_commit" in harness.events, (
            "run_perception_cycle ran before the claim transaction committed — "
            "the row lock is still held across the cycle"
        )
        return {"status": "completed", "events": 2}

    orchestrator.run_perception_cycle = AsyncMock(side_effect=_cycle)

    scheduler = SchedulerLoop(_mock_settings(), orchestrator=orchestrator)

    with (
        patch(
            "src.services.perception_policy.PerceptionPolicyService",
            return_value=harness.svc,
        ),
        patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
    ):
        await scheduler._tick_perception(harness.factory)

    # Ordering: claim_commit strictly precedes the first cycle.
    assert harness.events.index("claim_commit") < harness.events.index("cycle")
    harness.claim_db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. Lease prevents re-pick: claim advances next_run_at by LEASE_TTL_S
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_leases_source_out_of_due_window():
    """claim_due_sources must clear pending_run AND push next_run_at out by
    LEASE_TTL_S so a re-query within the lease window would not re-pick it."""
    db = MagicMock()
    db.flush = AsyncMock()
    svc = PerceptionPolicyService(db)

    state = _make_state(pending_run=True)
    now = datetime.now(timezone.utc)

    claimed = await svc.claim_due_sources([state], now=now)

    assert len(claimed) == 1
    assert claimed[0].state_id == "pst_test"
    assert claimed[0].source == "slack"
    assert claimed[0].user_id == "usr_test"
    assert claimed[0].workspace_id == "ws_test"

    # Lease applied to the ORM row.
    assert state.pending_run is False
    assert state.next_run_at == now + timedelta(seconds=LEASE_TTL_S)
    # The lease is in the future relative to "now": a re-query (next_run_at <= now)
    # plus pending_run=False means this source is no longer due.
    assert state.next_run_at > now


# ---------------------------------------------------------------------------
# 3. Crash/cancel mid-cycle → retried after lease (next_run_at ~ claim + lease)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_mid_cycle_leaves_source_due_after_lease():
    """If the cycle raises AND outcome recording is also lost (worst case), the
    leased next_run_at (claim_time + LEASE_TTL_S) is what makes the source due
    again — no held lock required for crash recovery."""
    db = MagicMock()
    db.flush = AsyncMock()
    svc = PerceptionPolicyService(db)

    state = _make_state(pending_run=True)
    claim_time = datetime.now(timezone.utc)

    await svc.claim_due_sources([state], now=claim_time)

    # Simulate crash: no record_success/record_failure runs. State is untouched
    # beyond the lease.
    assert state.pending_run is False
    expected_due = claim_time + timedelta(seconds=LEASE_TTL_S)
    assert state.next_run_at == expected_due

    # After the lease elapses, the row satisfies the due predicate again.
    after_lease = expected_due + timedelta(seconds=1)
    assert state.next_run_at <= after_lease
    # No corruption: counters unchanged, circuit closed.
    assert state.consecutive_failures == 0
    assert state.total_runs == 0
    assert state.circuit_state == "closed"


# ---------------------------------------------------------------------------
# 4. Outcome recording still correct through the new fresh-transaction structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outcome_recording_success_in_fresh_transaction():
    """A successful cycle re-fetches the state by id and calls record_success in
    its own committed transaction."""
    from src.services.scheduler import SchedulerLoop

    state = _make_state()
    harness = _TickHarness([state])

    orchestrator = MagicMock()
    orchestrator._budget = _mock_budget()
    orchestrator.run_perception_cycle = AsyncMock(return_value={"status": "completed", "events": 4})

    scheduler = SchedulerLoop(_mock_settings(), orchestrator=orchestrator)

    with (
        patch(
            "src.services.perception_policy.PerceptionPolicyService",
            return_value=harness.svc,
        ),
        patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
    ):
        await scheduler._tick_perception(harness.factory)

    harness.svc.get_by_state_id.assert_awaited_with("pst_test")
    harness.svc.record_success.assert_awaited_once()
    args, kwargs = harness.svc.record_success.await_args
    assert args[0] is state
    assert args[1] == 4
    assert kwargs.get("budget_multiplier") == 1
    # Recording committed in its own transaction.
    assert "record_commit" in harness.events


@pytest.mark.asyncio
async def test_outcome_recording_failure_in_fresh_transaction():
    """An error-status cycle records a failure in its own committed transaction."""
    from src.services.scheduler import SchedulerLoop

    state = _make_state()
    harness = _TickHarness([state])

    orchestrator = MagicMock()
    orchestrator._budget = _mock_budget()
    orchestrator.run_perception_cycle = AsyncMock(
        return_value={"status": "error", "error": "401 unauthorized", "events": 0}
    )

    scheduler = SchedulerLoop(_mock_settings(), orchestrator=orchestrator)

    with (
        patch(
            "src.services.perception_policy.PerceptionPolicyService",
            return_value=harness.svc,
        ),
        patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
    ):
        await scheduler._tick_perception(harness.factory)

    harness.svc.record_failure.assert_awaited_once()
    args, _ = harness.svc.record_failure.await_args
    assert args[0] is state
    assert "401" in args[1]
    assert "record_commit" in harness.events


@pytest.mark.asyncio
async def test_per_source_recording_isolation():
    """Two sources: one fails recording, the other still records — failures are
    isolated per source because each records in its own transaction."""
    from src.services.scheduler import SchedulerLoop

    s1 = _make_state(state_id="pst_a", source="github")
    s2 = _make_state(state_id="pst_b", source="slack")
    harness = _TickHarness([s1, s2])

    orchestrator = MagicMock()
    orchestrator._budget = _mock_budget()
    orchestrator.run_cross_source_synthesis = AsyncMock()

    async def _cycle(source, *, user_id, workspace_id):
        return {"status": "completed", "events": 1}

    orchestrator.run_perception_cycle = AsyncMock(side_effect=_cycle)

    # Make recording for the first state blow up; the second must still record.
    recorded: list[str] = []

    async def _record_success(state, event_count, budget_multiplier=1):
        if state.state_id == "pst_a":
            raise RuntimeError("record blew up")
        recorded.append(state.state_id)

    harness.svc.record_success = AsyncMock(side_effect=_record_success)

    scheduler = SchedulerLoop(_mock_settings(), orchestrator=orchestrator)

    with (
        patch(
            "src.services.perception_policy.PerceptionPolicyService",
            return_value=harness.svc,
        ),
        patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
    ):
        # Must not raise even though one recording failed.
        await scheduler._tick_perception(harness.factory)

    assert "pst_b" in recorded


# ---------------------------------------------------------------------------
# Mid-cycle signal: the row is unlocked during the cycle, so a webhook/intent
# can set pending_run while it runs. Recording must not swallow that signal.
# ---------------------------------------------------------------------------


async def _record_success_clears(state, event_count, budget_multiplier=1):
    """Faithful stand-in for the real record_success side effects."""
    state.pending_run = False
    state.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=300)


@pytest.mark.asyncio
async def test_signal_arriving_mid_cycle_is_re_armed():
    """A wakeup signal (fresh signal_at) that lands AFTER the claim must survive
    recording: the source is re-armed (pending_run=True, next_run_at<=now)."""
    from src.services.scheduler import SchedulerLoop

    state = _make_state(signal_at=None)  # no signal at claim time
    harness = _TickHarness([state])
    harness.svc.record_success = AsyncMock(side_effect=_record_success_clears)

    signal_time = datetime.now(timezone.utc)

    async def _cycle(source, *, user_id, workspace_id):
        # Webhook delivers a fresh signal DURING the cycle (row is unlocked).
        state.signal_at = signal_time
        state.pending_run = True
        return {"status": "completed", "events": 1}

    orchestrator = MagicMock()
    orchestrator._budget = _mock_budget()
    orchestrator.run_perception_cycle = AsyncMock(side_effect=_cycle)

    scheduler = SchedulerLoop(_mock_settings(), orchestrator=orchestrator)
    with (
        patch("src.services.perception_policy.PerceptionPolicyService", return_value=harness.svc),
        patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
    ):
        await scheduler._tick_perception(harness.factory)

    # record_success cleared pending_run + pushed next_run_at out; the re-arm must
    # have restored it because a newer signal_at arrived after the claim.
    assert state.pending_run is True
    assert state.next_run_at <= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_no_mid_cycle_signal_does_not_re_arm():
    """No signal during the cycle → no spurious re-arm; recording's next_run_at
    push stands."""
    from src.services.scheduler import SchedulerLoop

    state = _make_state(signal_at=None)
    harness = _TickHarness([state])
    harness.svc.record_success = AsyncMock(side_effect=_record_success_clears)

    orchestrator = MagicMock()
    orchestrator._budget = _mock_budget()
    orchestrator.run_perception_cycle = AsyncMock(return_value={"status": "completed", "events": 1})

    scheduler = SchedulerLoop(_mock_settings(), orchestrator=orchestrator)
    with (
        patch("src.services.perception_policy.PerceptionPolicyService", return_value=harness.svc),
        patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
    ):
        await scheduler._tick_perception(harness.factory)

    assert state.pending_run is False
    assert state.next_run_at > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Validity gating: a source whose OAuth provider is permanently unusable
# (no_token / revoked) must be dropped from the runnable list and never polled
# (stops orphaned auth-failure churn). Detailed reason/notify behaviour lives in
# test_perception_reauth_gate.py; here we assert the tick-level effect: the bad
# source is not run, the good one is.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_token_source_is_dropped_and_not_run():
    from src.services.oauth_manager import TokenResult
    from src.services.scheduler import SchedulerLoop

    s_ok = _make_state(state_id="pst_ok", source="slack", user_id="usr_ok")
    s_orphan = _make_state(state_id="pst_orphan", source="slack", user_id="usr_orphan")
    harness = _TickHarness([s_ok, s_orphan])

    # OAuthManager: usr_ok valid, usr_orphan revoked. ReauthService is a no-op
    # stub (notify path covered in test_perception_reauth_gate.py).
    async def _validity(user_id, provider):
        if user_id == "usr_ok":
            return TokenResult(token="tok", reason="ok")
        return TokenResult(token=None, reason="revoked")

    oauth = MagicMock()
    oauth.get_valid_token_with_reason = AsyncMock(side_effect=_validity)
    reauth = MagicMock()
    # _drop_tokenless_sources applies the writes on the caller's locked session
    # via apply_needs_reauth (commit/notify deferred to the tick), not the
    # self-committing mark_needs_reauth convenience wrapper.
    reauth.apply_needs_reauth = AsyncMock()

    cycles: list[str] = []

    async def _cycle(source, *, user_id, workspace_id):
        cycles.append(user_id)
        return {"status": "completed", "events": 0}

    orchestrator = MagicMock()
    orchestrator._budget = _mock_budget()
    orchestrator.run_perception_cycle = AsyncMock(side_effect=_cycle)

    scheduler = SchedulerLoop(_mock_settings(), orchestrator=orchestrator)
    with (
        patch("src.services.perception_policy.PerceptionPolicyService", return_value=harness.svc),
        patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
        patch.object(scheduler, "_validity_gate_collaborators", return_value=(oauth, reauth)),
    ):
        await scheduler._tick_perception(harness.factory)

    assert cycles == ["usr_ok"]  # only the valid-token source was polled
    reauth.apply_needs_reauth.assert_awaited_once()  # the revoked source surfaced re-auth


# ---------------------------------------------------------------------------
# Task 12: one turn_scope per perception cycle, not one per source
# ---------------------------------------------------------------------------


async def test_run_due_cycles_opens_exactly_one_turn_scope():
    """MCP sessions opened during a poll must be torn down at cycle end.

    TurnScope's docstring warns that detached background work — the scheduler is
    exactly that — must not open turn-scoped sessions without a scope, or they
    survive until the idle reaper. Gmail and Calendar share the
    google-workspace SessionKey, so ONE refcounted scope around the whole
    due-sources loop serves both. One scope per user, not per source.
    """
    from src.orchestrator.perception import PerceptionCoordinator

    orchestrator = MagicMock()
    orchestrator.run_perception_cycle = AsyncMock(return_value={"status": "ok", "events": 0})
    orchestrator._publish_event = AsyncMock()

    state_a = MagicMock()
    state_a.source = "gmail"
    state_b = MagicMock()
    state_b.source = "calendar"

    svc = AsyncMock()
    svc.get_due_sources = AsyncMock(return_value=[state_a, state_b])
    svc.record_success = AsyncMock()
    svc.record_failure = AsyncMock()

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_factory_fn = MagicMock(return_value=mock_cm)

    scope_cm = MagicMock()
    scope_cm.__aenter__ = AsyncMock(return_value=None)
    scope_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.models.database.get_session_factory", return_value=mock_factory_fn),
        patch("src.services.perception_policy.PerceptionPolicyService", return_value=svc),
        patch(
            "src.orchestrator.perception.turn_scope", MagicMock(return_value=scope_cm)
        ) as mock_scope,
    ):
        coord = PerceptionCoordinator(orchestrator, user_id=TEST_USER_ID, workspace_id="ws_test")
        await coord.run_due_cycles()

    assert orchestrator.run_perception_cycle.await_count == 2, "both sources should run"
    assert mock_scope.call_count == 1, (
        f"expected ONE turn_scope for the whole cycle, got {mock_scope.call_count}"
    )
    scope_cm.__aexit__.assert_awaited_once()
