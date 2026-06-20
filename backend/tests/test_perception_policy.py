"""Tests for PerceptionPolicyService — signal-driven perception guardrails."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.perception_state import PerceptionState
from src.services.perception_policy import (
    CIRCUIT_COOLDOWN_S,
    CIRCUIT_FAILURE_THRESHOLD,
    DEFAULT_INTERVALS,
    MAX_INTERVAL_S,
    MIN_INTERVAL_S,
    PERMANENT_FAILURE_THRESHOLD,
    STARVATION_CEILING_S,
    TRANSIENT_FAILURE_THRESHOLD,
    PerceptionPolicyService,
    classify_error,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    source: str = "gmail",
    mode: str = "poll",
    base_interval_s: int = 300,
    effective_interval_s: int = 300,
    next_run_at: datetime | None = None,
    last_run_at: datetime | None = None,
    consecutive_failures: int = 0,
    circuit_state: str = "closed",
    circuit_opened_at: datetime | None = None,
    pending_run: bool = False,
    agent_interval_s: int | None = None,
    total_runs: int = 0,
    last_event_count: int = 0,
) -> PerceptionState:
    """Create an in-memory PerceptionState for testing."""
    return PerceptionState(
        state_id="pst_test",
        workspace_id="ws_test",
        user_id="usr_test",
        source=source,
        mode=mode,
        base_interval_s=base_interval_s,
        effective_interval_s=effective_interval_s,
        next_run_at=next_run_at,
        last_run_at=last_run_at,
        agent_interval_s=agent_interval_s,
        watch_entities=None,
        consecutive_failures=consecutive_failures,
        last_error=None,
        circuit_state=circuit_state,
        circuit_opened_at=circuit_opened_at,
        pending_run=pending_run,
        signal_source=None,
        signal_at=None,
        last_event_count=last_event_count,
        total_runs=total_runs,
    )


def _mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


def _mock_scalar_result(states: list[PerceptionState]):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = states
    result.scalars.return_value = scalars
    return result


# ---------------------------------------------------------------------------
# Effective interval computation
# ---------------------------------------------------------------------------


class TestComputeEffectiveInterval:
    def test_base_interval_no_override(self):
        state = _make_state(base_interval_s=300)
        svc = PerceptionPolicyService(_mock_db())
        assert svc._compute_effective_interval(state) == 300

    def test_agent_override_takes_precedence(self):
        state = _make_state(base_interval_s=300, agent_interval_s=120)
        svc = PerceptionPolicyService(_mock_db())
        assert svc._compute_effective_interval(state) == 120

    def test_clamps_below_min(self):
        state = _make_state(base_interval_s=10)
        svc = PerceptionPolicyService(_mock_db())
        assert svc._compute_effective_interval(state) == MIN_INTERVAL_S

    def test_clamps_above_max(self):
        state = _make_state(base_interval_s=5000)
        svc = PerceptionPolicyService(_mock_db())
        assert svc._compute_effective_interval(state) == MAX_INTERVAL_S

    def test_backoff_on_failures(self):
        state = _make_state(base_interval_s=300, consecutive_failures=2)
        svc = PerceptionPolicyService(_mock_db())
        # 300 * 2^2 = 1200
        assert svc._compute_effective_interval(state) == 1200

    def test_backoff_capped(self):
        state = _make_state(base_interval_s=300, consecutive_failures=10)
        svc = PerceptionPolicyService(_mock_db())
        # 300 * BACKOFF_CAP(8) = 2400
        assert svc._compute_effective_interval(state) == 2400

    def test_agent_override_with_backoff(self):
        state = _make_state(agent_interval_s=120, consecutive_failures=1)
        svc = PerceptionPolicyService(_mock_db())
        # 120 * 2^1 = 240
        assert svc._compute_effective_interval(state) == 240


# ---------------------------------------------------------------------------
# Next run computation
# ---------------------------------------------------------------------------


class TestComputeNextRun:
    def test_basic_next_run(self):
        now = datetime.now(timezone.utc)
        state = _make_state(effective_interval_s=300, last_run_at=now)
        svc = PerceptionPolicyService(_mock_db())
        next_run = svc._compute_next_run(state)
        # Should be ~300 seconds from now
        delta = (next_run - now).total_seconds()
        assert 299 <= delta <= 301

    def test_budget_multiplier_stretches(self):
        now = datetime.now(timezone.utc)
        state = _make_state(effective_interval_s=300, last_run_at=now)
        svc = PerceptionPolicyService(_mock_db())
        next_run = svc._compute_next_run(state, budget_multiplier=3)
        delta = (next_run - now).total_seconds()
        assert 899 <= delta <= 901

    def test_starvation_ceiling_forces_immediate_run(self):
        long_ago = datetime.now(timezone.utc) - timedelta(seconds=STARVATION_CEILING_S + 100)
        state = _make_state(effective_interval_s=300, last_run_at=long_ago)
        svc = PerceptionPolicyService(_mock_db())
        next_run = svc._compute_next_run(state)
        # Should be essentially now (within a second)
        delta = (next_run - datetime.now(timezone.utc)).total_seconds()
        assert delta <= 1

    def test_no_last_run_uses_interval(self):
        state = _make_state(effective_interval_s=300, last_run_at=None)
        svc = PerceptionPolicyService(_mock_db())
        next_run = svc._compute_next_run(state)
        delta = (next_run - datetime.now(timezone.utc)).total_seconds()
        assert 299 <= delta <= 301


# ---------------------------------------------------------------------------
# Record success
# ---------------------------------------------------------------------------


class TestRecordSuccess:
    @pytest.mark.asyncio
    async def test_resets_failures_and_computes_next_run(self):
        db = _mock_db()
        state = _make_state(consecutive_failures=2, circuit_state="half_open")
        svc = PerceptionPolicyService(db)

        result = await svc.record_success(state, event_count=5)

        assert result.consecutive_failures == 0
        assert result.last_error is None
        assert result.circuit_state == "closed"
        assert result.last_event_count == 5
        assert result.total_runs == 1
        assert result.pending_run is False
        assert result.last_run_at is not None
        assert result.next_run_at is not None
        db.flush.assert_awaited()

    @pytest.mark.asyncio
    async def test_increments_total_runs(self):
        db = _mock_db()
        state = _make_state(total_runs=10)
        svc = PerceptionPolicyService(db)

        result = await svc.record_success(state, event_count=0)
        assert result.total_runs == 11

    @pytest.mark.asyncio
    async def test_budget_multiplier_stretches_next_run(self):
        """budget_multiplier must thread from record_success into next_run_at.

        Without the multiplier the next run is ~base interval away; with a
        multiplier of 2 it should be ~2x further out (less frequent polling
        under budget pressure). Asserts the ratio rather than exact seconds to
        tolerate the sub-second wall-clock drift between the two calls.
        """
        svc = PerceptionPolicyService(_mock_db())

        baseline_state = _make_state(base_interval_s=300, effective_interval_s=300)
        baseline = await svc.record_success(baseline_state, event_count=0, budget_multiplier=1)
        baseline_delta = (baseline.next_run_at - baseline.last_run_at).total_seconds()

        stretched_state = _make_state(base_interval_s=300, effective_interval_s=300)
        stretched = await svc.record_success(stretched_state, event_count=0, budget_multiplier=2)
        stretched_delta = (stretched.next_run_at - stretched.last_run_at).total_seconds()

        # Stretched interval should be ~2x the baseline (allow small tolerance).
        assert stretched_delta >= baseline_delta * 1.9
        assert stretched_delta <= baseline_delta * 2.1


# ---------------------------------------------------------------------------
# Record failure
# ---------------------------------------------------------------------------


class TestRecordFailure:
    @pytest.mark.asyncio
    async def test_increments_failure_count(self):
        db = _mock_db()
        state = _make_state(consecutive_failures=1)
        svc = PerceptionPolicyService(db)

        result = await svc.record_failure(state, "timeout")

        assert result.consecutive_failures == 2
        assert result.last_error == "timeout"
        assert result.pending_run is False

    @pytest.mark.asyncio
    async def test_opens_circuit_at_threshold(self):
        db = _mock_db()
        state = _make_state(consecutive_failures=CIRCUIT_FAILURE_THRESHOLD - 1)
        svc = PerceptionPolicyService(db)

        # Use "unknown"-classified error so default threshold (3) applies
        result = await svc.record_failure(state, "unknown internal error")

        assert result.circuit_state == "open"
        assert result.circuit_opened_at is not None

    @pytest.mark.asyncio
    async def test_does_not_open_circuit_below_threshold(self):
        db = _mock_db()
        state = _make_state(consecutive_failures=0)
        svc = PerceptionPolicyService(db)

        result = await svc.record_failure(state, "transient")

        assert result.circuit_state == "closed"
        assert result.next_run_at is not None  # still scheduled

    @pytest.mark.asyncio
    async def test_truncates_long_error(self):
        db = _mock_db()
        state = _make_state()
        svc = PerceptionPolicyService(db)
        long_error = "x" * 1000

        result = await svc.record_failure(state, long_error)
        assert len(result.last_error) == 512


# ---------------------------------------------------------------------------
# Request run (signal handling)
# ---------------------------------------------------------------------------


class TestRequestRun:
    @pytest.mark.asyncio
    async def test_sets_pending_run(self):
        db = _mock_db()
        state = _make_state(pending_run=False)

        # Mock get_or_create_state to return our state
        svc = PerceptionPolicyService(db)
        svc.get_or_create_state = AsyncMock(return_value=state)

        result = await svc.request_run("ws_test", "usr_test", "gmail", "webhook")

        assert result.pending_run is True
        assert result.signal_source == "webhook"
        assert result.signal_at is not None

    @pytest.mark.asyncio
    async def test_wakes_paused_source_on_user_intent_signal(self):
        """A user-driven activation signal still un-pauses a paused source."""
        db = _mock_db()
        state = _make_state(mode="paused", pending_run=False)
        svc = PerceptionPolicyService(db)
        svc.get_or_create_state = AsyncMock(return_value=state)

        result = await svc.request_run("ws_test", "usr_test", "gmail", "user_intent")

        assert result.mode == "active"
        assert result.pending_run is True

    @pytest.mark.asyncio
    async def test_webhook_does_not_resurrect_paused_source(self):
        """SECURITY (flaw 5): a webhook wake must NOT un-pause a paused source.

        A paused source represents an explicit/lifecycle decision to stop
        polling. A forged or even valid inbound webhook is an untrusted external
        signal and must not override that — the source stays paused and no run is
        scheduled. The poll/lifecycle path remains the only way to resume it.
        """
        db = _mock_db()
        state = _make_state(mode="paused", pending_run=False)
        svc = PerceptionPolicyService(db)
        svc.get_or_create_state = AsyncMock(return_value=state)

        result = await svc.request_run("ws_test", "usr_test", "gmail", "webhook")

        assert result.mode == "paused"
        assert result.pending_run is False

    @pytest.mark.asyncio
    async def test_webhook_wakes_active_source(self):
        """A webhook on an ALREADY-active source still sets pending_run."""
        db = _mock_db()
        state = _make_state(mode="active", pending_run=False)
        svc = PerceptionPolicyService(db)
        svc.get_or_create_state = AsyncMock(return_value=state)

        result = await svc.request_run("ws_test", "usr_test", "gmail", "webhook")

        assert result.mode == "active"
        assert result.pending_run is True
        assert result.signal_source == "webhook"

    @pytest.mark.asyncio
    async def test_does_not_wake_paused_source_for_non_activation_signal(self):
        db = _mock_db()
        state = _make_state(mode="paused", pending_run=False)
        svc = PerceptionPolicyService(db)
        svc.get_or_create_state = AsyncMock(return_value=state)

        result = await svc.request_run("ws_test", "usr_test", "gmail", "manual_probe")

        assert result.mode == "paused"
        assert result.pending_run is False

    @pytest.mark.asyncio
    async def test_records_signal_source(self):
        db = _mock_db()
        state = _make_state()
        svc = PerceptionPolicyService(db)
        svc.get_or_create_state = AsyncMock(return_value=state)

        await svc.request_run("ws_test", "usr_test", "gmail", "user_intent")
        assert state.signal_source == "user_intent"

        await svc.request_run("ws_test", "usr_test", "gmail", "webhook")
        assert state.signal_source == "webhook"


# ---------------------------------------------------------------------------
# Apply agent policy
# ---------------------------------------------------------------------------


class TestApplyAgentPolicy:
    @pytest.mark.asyncio
    async def test_sets_agent_interval_within_bounds(self):
        db = _mock_db()
        state = _make_state(base_interval_s=300)
        svc = PerceptionPolicyService(db)

        result = await svc.apply_agent_policy(state, next_check_seconds=120)

        assert result.agent_interval_s == 120
        assert result.effective_interval_s == 120
        assert result.next_run_at is not None

    @pytest.mark.asyncio
    async def test_clamps_below_min(self):
        db = _mock_db()
        state = _make_state()
        svc = PerceptionPolicyService(db)

        result = await svc.apply_agent_policy(state, next_check_seconds=5)
        assert result.agent_interval_s == MIN_INTERVAL_S

    @pytest.mark.asyncio
    async def test_clamps_above_max(self):
        db = _mock_db()
        state = _make_state()
        svc = PerceptionPolicyService(db)

        result = await svc.apply_agent_policy(state, next_check_seconds=9999)
        assert result.agent_interval_s == MAX_INTERVAL_S

    @pytest.mark.asyncio
    async def test_updates_watch_entities(self):
        db = _mock_db()
        state = _make_state()
        svc = PerceptionPolicyService(db)

        result = await svc.apply_agent_policy(
            state, watch_entities=["ent_investor1", "ent_partner2"]
        )
        assert result.watch_entities == ["ent_investor1", "ent_partner2"]


# ---------------------------------------------------------------------------
# Circuit breaker reopening
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_reopens_after_cooldown(self):
        opened = datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_COOLDOWN_S + 10)
        state = _make_state(
            circuit_state="open",
            circuit_opened_at=opened,
            consecutive_failures=5,
        )
        svc = PerceptionPolicyService(_mock_db())
        svc._maybe_reopen_circuit(state, datetime.now(timezone.utc))

        assert state.circuit_state == "half_open"
        assert state.consecutive_failures == 0

    def test_stays_open_before_cooldown(self):
        opened = datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_COOLDOWN_S - 60)
        state = _make_state(
            circuit_state="open",
            circuit_opened_at=opened,
            consecutive_failures=5,
        )
        svc = PerceptionPolicyService(_mock_db())
        svc._maybe_reopen_circuit(state, datetime.now(timezone.utc))

        assert state.circuit_state == "open"
        assert state.consecutive_failures == 5

    def test_noop_if_not_open(self):
        state = _make_state(circuit_state="closed")
        svc = PerceptionPolicyService(_mock_db())
        svc._maybe_reopen_circuit(state, datetime.now(timezone.utc))
        assert state.circuit_state == "closed"


class TestDueSources:
    @pytest.mark.asyncio
    async def test_get_due_sources_uses_skip_locked(self):
        """Both due-source SELECTs must carry FOR UPDATE SKIP LOCKED.

        Two worker processes must not both claim the same PerceptionState row
        (duplicate polls/ingest). The query must lock claimed rows and skip
        rows already locked by a peer — mirroring the proven background-task
        pattern in scheduler/background_tasks_tick.py.
        """
        from sqlalchemy.dialects import postgresql

        captured: list[str] = []

        async def capturing_execute(stmt, *args, **kwargs):
            compiled = stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
            captured.append(str(compiled))
            return _mock_scalar_result([])

        db = _mock_db()
        db.execute = AsyncMock(side_effect=capturing_execute)
        svc = PerceptionPolicyService(db)

        await svc.get_due_sources_all_users()
        await svc.get_due_sources(user_id="usr_test")

        assert len(captured) == 2, "Expected one query per due-source method"
        for query in captured:
            lowered = query.lower()
            assert "for update" in lowered, f"Query missing FOR UPDATE: {query}"
            assert "skip locked" in lowered, f"Query missing SKIP LOCKED: {query}"

    @pytest.mark.asyncio
    async def test_reopens_open_circuit_without_next_run(self):
        db = _mock_db()
        cooled = datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_COOLDOWN_S + 30)
        state = _make_state(
            mode="poll",
            pending_run=False,
            next_run_at=None,
            circuit_state="open",
            circuit_opened_at=cooled,
        )
        db.execute = AsyncMock(return_value=_mock_scalar_result([state]))
        svc = PerceptionPolicyService(db)

        result = await svc.get_due_sources(user_id="usr_test")

        assert len(result) == 1
        assert result[0].circuit_state == "half_open"


# ---------------------------------------------------------------------------
# Default intervals
# ---------------------------------------------------------------------------


class TestDefaultIntervals:
    def test_known_sources_have_defaults(self):
        for source in ("gmail", "calendar", "slack", "github"):
            assert source in DEFAULT_INTERVALS

    def test_gmail_default(self):
        assert DEFAULT_INTERVALS["gmail"] == 300

    def test_calendar_default(self):
        assert DEFAULT_INTERVALS["calendar"] == 900


# ---------------------------------------------------------------------------
# Error classification (Phase 5)
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_transient_timeout(self):
        assert classify_error("Connection timed out after 60s") == "transient"

    def test_transient_rate_limit(self):
        assert classify_error("HTTP 429 Too Many Requests") == "transient"

    def test_transient_503(self):
        assert classify_error("503 Service Unavailable") == "transient"

    def test_transient_502(self):
        assert classify_error("502 Bad Gateway") == "transient"

    def test_transient_connection_reset(self):
        assert classify_error("ECONNRESET: connection reset by peer") == "transient"

    def test_permanent_401(self):
        assert classify_error("HTTP 401 Unauthorized") == "permanent"

    def test_permanent_403(self):
        assert classify_error("HTTP 403 Forbidden") == "permanent"

    def test_permanent_revoked_token(self):
        assert classify_error("OAuth token has been revoked") == "permanent"

    def test_permanent_invalid_credentials(self):
        assert classify_error("invalid_credentials: token expired") == "permanent"

    def test_permanent_access_denied(self):
        assert classify_error("Access denied for resource") == "permanent"

    def test_unknown_generic_error(self):
        assert classify_error("Something unexpected happened") == "unknown"

    def test_unknown_empty_string(self):
        assert classify_error("") == "unknown"

    def test_permanent_takes_priority_over_transient(self):
        """When error matches both patterns, permanent wins (checked first)."""
        # An error like "401 timeout" should be permanent since 401 is checked first
        assert classify_error("401 Unauthorized timeout") == "permanent"


class TestErrorClassAwareCircuitBreaker:
    @pytest.mark.asyncio
    async def test_permanent_error_opens_circuit_immediately(self):
        """Permanent errors should open the circuit after just 1 failure."""
        db = _mock_db()
        state = _make_state(consecutive_failures=0)
        svc = PerceptionPolicyService(db)

        result = await svc.record_failure(state, "HTTP 401 Unauthorized")

        assert result.consecutive_failures == PERMANENT_FAILURE_THRESHOLD
        assert result.circuit_state == "open"
        assert result.circuit_opened_at is not None

    @pytest.mark.asyncio
    async def test_transient_error_needs_more_failures(self):
        """Transient errors should NOT open circuit at default threshold (3)."""
        db = _mock_db()
        state = _make_state(consecutive_failures=CIRCUIT_FAILURE_THRESHOLD - 1)
        svc = PerceptionPolicyService(db)

        result = await svc.record_failure(state, "Connection timed out")

        # At 3 failures (default threshold), transient should NOT open
        # because transient threshold is 6
        assert result.consecutive_failures == CIRCUIT_FAILURE_THRESHOLD
        assert result.circuit_state == "closed"

    @pytest.mark.asyncio
    async def test_transient_opens_at_double_threshold(self):
        """Transient errors should open circuit at TRANSIENT_FAILURE_THRESHOLD."""
        db = _mock_db()
        state = _make_state(consecutive_failures=TRANSIENT_FAILURE_THRESHOLD - 1)
        svc = PerceptionPolicyService(db)

        result = await svc.record_failure(state, "503 Service Unavailable")

        assert result.consecutive_failures == TRANSIENT_FAILURE_THRESHOLD
        assert result.circuit_state == "open"

    @pytest.mark.asyncio
    async def test_unknown_error_uses_default_threshold(self):
        """Unknown errors should use the default CIRCUIT_FAILURE_THRESHOLD."""
        db = _mock_db()
        state = _make_state(consecutive_failures=CIRCUIT_FAILURE_THRESHOLD - 1)
        svc = PerceptionPolicyService(db)

        result = await svc.record_failure(state, "Something weird happened")

        assert result.consecutive_failures == CIRCUIT_FAILURE_THRESHOLD
        assert result.circuit_state == "open"

    @pytest.mark.asyncio
    async def test_missing_error_key_default_is_not_threshold_three(self):
        """The default error sentinel for a missing 'error' key must fail safe.

        Foot-gun: callers do ``result.get("error", DEFAULT)``. If DEFAULT is the
        bare string "unknown", a missing error key silently lands on the
        unknown/threshold-3 bucket. The shared default sentinel must instead
        classify as transient so a missing key never opens the circuit after 3.
        """
        from src.connectors.poll_result import MISSING_ERROR_SENTINEL

        assert classify_error(MISSING_ERROR_SENTINEL) == "transient"

        db = _mock_db()
        # Sit one below the *unknown* threshold; a transient-classified default
        # must NOT open the circuit here.
        state = _make_state(consecutive_failures=CIRCUIT_FAILURE_THRESHOLD - 1)
        svc = PerceptionPolicyService(db)

        result = await svc.record_failure(state, MISSING_ERROR_SENTINEL)

        assert result.consecutive_failures == CIRCUIT_FAILURE_THRESHOLD
        assert result.circuit_state == "closed"
