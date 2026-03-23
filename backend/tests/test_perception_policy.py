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
    STARVATION_CEILING_S,
    PerceptionPolicyService,
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

        result = await svc.record_failure(state, "connection_refused")

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
    async def test_does_not_wake_paused_source(self):
        db = _mock_db()
        state = _make_state(mode="paused", pending_run=False)
        svc = PerceptionPolicyService(db)
        svc.get_or_create_state = AsyncMock(return_value=state)

        result = await svc.request_run("ws_test", "usr_test", "gmail", "webhook")

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
