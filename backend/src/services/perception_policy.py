"""Perception Policy Service — system guardrails for signal-driven perception.

Owns the rules for when each source should next be checked.  Signals
(webhooks, user intent, agent requests) set ``pending_run``; the scheduler
picks up due rows; after each cycle the agent-informed policy updates
``next_run_at``.

Hard guardrails this service enforces:
- Per-source min/max polling interval
- Circuit breaker on consecutive failures
- Starvation prevention (max silence window)
- Budget-aware interval stretching
- Deduplication of repeated wakeup signals
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.perception_state import PerceptionState

logger = logging.getLogger(__name__)

ErrorClass = Literal["transient", "permanent", "unknown"]

# ---------------------------------------------------------------------------
# Guardrail constants
# ---------------------------------------------------------------------------
MIN_INTERVAL_S = 60  # never poll faster than 1 min
MAX_INTERVAL_S = 3600  # never go longer than 1 hour
CIRCUIT_COOLDOWN_S = 300  # 5 min circuit breaker cooldown
CIRCUIT_FAILURE_THRESHOLD = 3
STARVATION_CEILING_S = 1800  # force run after 30 min silence
BACKOFF_CAP = 8  # max backoff multiplier (2^3)

# Provisional lease applied to a source when it is CLAIMED (pending_run cleared,
# next_run_at pushed out) so the row lock can be released *before* the long
# LLM/MCP perception cycle runs. The scheduler holds the FOR UPDATE lock only
# for the claim transaction; once committed the lease — not a held lock — is
# what prevents the next tick (or a peer worker) from double-picking the source.
#
# LEASE_TTL_S MUST exceed the scheduler sub-tick timeout (~90s): a perception
# cycle is force-cancelled at the sub-tick timeout, so as long as the lease is
# longer than that timeout a cycle can never outlive its own lease and cause a
# double-pick. On crash/cancel/timeout (no outcome recorded) the source simply
# becomes due again once the lease expires — preserving crash recovery without
# relying on holding the row lock across the cycle.
LEASE_TTL_S = 180

# Poll cadence per source. NOT AN ALLOWLIST — ``get_or_create_state`` reads it
# with ``.get(source, 300)``, so an unlisted source is provisioned silently at
# the 300s default rather than rejected. Do not cite a source's absence from
# this map as evidence that the source cannot be polled; what actually holds an
# unlisted source shut is that every writer creates its row ``mode="paused"``
# and the due-source queries filter ``mode != "paused"``.
DEFAULT_INTERVALS: dict[str, int] = {
    "gmail": 300,
    "calendar": 900,
    "slack": 300,
    "github": 600,
    # Pages change far less often than mail, and every poll walks the workspace
    # newest-first rather than reading a server-side window, so a slower cadence
    # is both cheaper and no less timely.
    "notion": 900,
}

# Error-class-aware circuit breaker thresholds
TRANSIENT_FAILURE_THRESHOLD = 6  # double normal — transient errors self-heal
PERMANENT_FAILURE_THRESHOLD = 1  # open immediately — retrying won't help

# Regex patterns for error classification
_TRANSIENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"timed?\s*out", re.IGNORECASE),
    re.compile(r"\b429\b"),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"\b503\b"),
    re.compile(r"service.?unavailable", re.IGNORECASE),
    re.compile(r"\b502\b"),
    re.compile(r"bad.?gateway", re.IGNORECASE),
    re.compile(r"\b504\b"),
    re.compile(r"gateway.?timeout", re.IGNORECASE),
    re.compile(r"connection.?(reset|refused|aborted)", re.IGNORECASE),
    re.compile(r"temporary.?failure", re.IGNORECASE),
    re.compile(r"ECONNRESET|ECONNREFUSED|ETIMEDOUT", re.IGNORECASE),
]

_PERMANENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b401\b"),
    re.compile(r"unauthorized", re.IGNORECASE),
    re.compile(r"\b403\b"),
    re.compile(r"forbidden", re.IGNORECASE),
    re.compile(r"revoked", re.IGNORECASE),
    re.compile(r"invalid.?(token|credential|key|grant)", re.IGNORECASE),
    re.compile(r"token.?expired", re.IGNORECASE),
    re.compile(r"access.?denied", re.IGNORECASE),
    re.compile(r"not.?authorized", re.IGNORECASE),
    re.compile(r"permission.?denied", re.IGNORECASE),
    re.compile(r"scope.?not.?granted", re.IGNORECASE),
    # Matches the sentinel produced by error_class_to_policy_error("permanent") so
    # that connector-reported permanent errors (unrecoverable 4xx) open the circuit
    # after 1 failure rather than falling through to the unknown threshold of 3.
    re.compile(r"\bpermanent\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class ClaimedSource:
    """Detached, plain-data snapshot of a leased perception source.

    Captured inside the short CLAIM transaction so phase 2 can run cycles after
    the row lock has been released (no ORM instance held across the long cycle).
    """

    state_id: str
    source: str
    user_id: str
    workspace_id: str


def classify_error(error: str) -> ErrorClass:
    """Classify an error string into transient, permanent, or unknown.

    Pattern-matches against known error signatures to determine whether
    retrying is likely to succeed (transient), will never succeed (permanent),
    or is uncertain (unknown).
    """
    if not error:
        return "unknown"

    for pat in _PERMANENT_PATTERNS:
        if pat.search(error):
            return "permanent"

    for pat in _TRANSIENT_PATTERNS:
        if pat.search(error):
            return "transient"

    return "unknown"


def _threshold_for_error_class(error_class: ErrorClass) -> int:
    """Return the circuit breaker failure threshold for an error class."""
    if error_class == "permanent":
        return PERMANENT_FAILURE_THRESHOLD
    if error_class == "transient":
        return TRANSIENT_FAILURE_THRESHOLD
    return CIRCUIT_FAILURE_THRESHOLD


class PerceptionPolicyService:
    """System guardrails layer for perception scheduling.

    Does NOT make agent calls — it enforces constraints on whatever the
    agent or signals request.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        # Signals that may RESUME a paused source. A ``webhook`` is intentionally
        # NOT here: an inbound webhook is an untrusted external signal and must
        # not resurrect a source that was deliberately paused (explicit/admin/
        # user/lifecycle pause). It may only wake an already-active source (see
        # request_run). The poll/lifecycle path is the sole resume route.
        self._wake_signals = {"user_intent", "agent", "bootstrap", "manual_poll"}

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    async def get_or_create_state(
        self, workspace_id: str, user_id: str, source: str
    ) -> PerceptionState:
        """Fetch existing state or create with defaults."""
        stmt = select(PerceptionState).where(
            PerceptionState.workspace_id == workspace_id,
            PerceptionState.user_id == user_id,
            PerceptionState.source == source,
        )
        result = await self._db.execute(stmt)
        state = result.scalar_one_or_none()
        if state is not None:
            return state

        base = DEFAULT_INTERVALS.get(source, 300)
        state = PerceptionState(
            workspace_id=workspace_id,
            user_id=user_id,
            source=source,
            mode="paused",
            base_interval_s=base,
            effective_interval_s=base,
        )
        self._db.add(state)
        await self._db.flush()
        return state

    # ------------------------------------------------------------------
    # Due-source queries
    # ------------------------------------------------------------------

    async def get_due_sources(self, user_id: str) -> list[PerceptionState]:
        """Return sources due for a single user."""
        now = datetime.now(timezone.utc)
        reopen_before = now - timedelta(seconds=CIRCUIT_COOLDOWN_S)
        stmt = (
            select(PerceptionState)
            .where(
                PerceptionState.user_id == user_id,
                PerceptionState.mode != "paused",
            )
            .where(
                (PerceptionState.pending_run.is_(True))
                | (PerceptionState.next_run_at.isnot(None) & (PerceptionState.next_run_at <= now))
                | (
                    and_(
                        PerceptionState.circuit_state == "open",
                        PerceptionState.circuit_opened_at.isnot(None),
                        PerceptionState.circuit_opened_at <= reopen_before,
                    )
                )
            )
            .order_by(PerceptionState.next_run_at.asc().nullslast())
            # Claim rows so two scheduler workers can't double-pick the same
            # source (duplicate polls/ingest). Locks release when the caller's
            # transaction commits/rolls back per tick.
            .with_for_update(skip_locked=True)
        )
        result = await self._db.execute(stmt)
        states = list(result.scalars().all())

        # Reopen circuits that have cooled down, then filter to runnable rows.
        for s in states:
            if s.circuit_state == "half_open":
                continue  # allow trial run
            self._maybe_reopen_circuit(s, now)

        return [s for s in states if s.circuit_state != "open"]

    async def get_due_sources_all_users(self) -> list[PerceptionState]:
        """Return due sources across all users (used by scheduler)."""
        now = datetime.now(timezone.utc)
        reopen_before = now - timedelta(seconds=CIRCUIT_COOLDOWN_S)
        stmt = (
            select(PerceptionState)
            .where(
                PerceptionState.mode != "paused",
            )
            .where(
                (PerceptionState.pending_run.is_(True))
                | (PerceptionState.next_run_at.isnot(None) & (PerceptionState.next_run_at <= now))
                | (
                    and_(
                        PerceptionState.circuit_state == "open",
                        PerceptionState.circuit_opened_at.isnot(None),
                        PerceptionState.circuit_opened_at <= reopen_before,
                    )
                )
            )
            .order_by(PerceptionState.next_run_at.asc().nullslast())
            # Claim rows so two scheduler workers can't double-pick the same
            # source (duplicate polls/ingest). Locks release when the caller's
            # transaction commits/rolls back per tick.
            .with_for_update(skip_locked=True)
        )
        result = await self._db.execute(stmt)
        states = list(result.scalars().all())

        for s in states:
            self._maybe_reopen_circuit(s, now)

        return [s for s in states if s.circuit_state != "open"]

    async def claim_due_sources(
        self, sources: list[PerceptionState], now: datetime | None = None
    ) -> list[ClaimedSource]:
        """Lease the given due sources so the row lock can be released early.

        For each source: clear ``pending_run`` (so the next tick won't re-pick it
        on the pending flag) and push ``next_run_at`` out by ``LEASE_TTL_S`` (a
        provisional lease, so the due-window query won't re-pick it either). The
        caller is expected to ``commit`` immediately after this returns —
        releasing the ``FOR UPDATE`` lock — and only THEN run the long cycles.

        Returns plain, detached-safe snapshots so callers never touch the ORM
        instances after the claim transaction is closed.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=LEASE_TTL_S)

        claimed: list[ClaimedSource] = []
        for state in sources:
            state.pending_run = False
            state.next_run_at = lease_until
            claimed.append(
                ClaimedSource(
                    state_id=state.state_id,
                    source=state.source,
                    user_id=state.user_id,
                    workspace_id=state.workspace_id or "",
                )
            )
        await self._db.flush()
        return claimed

    async def get_by_state_id(self, state_id: str) -> PerceptionState | None:
        """Re-fetch a perception state by primary key (for per-source recording)."""
        stmt = select(PerceptionState).where(PerceptionState.state_id == state_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Lifecycle: success / failure / signal
    # ------------------------------------------------------------------

    async def record_success(
        self,
        state: PerceptionState,
        event_count: int,
        budget_multiplier: int = 1,
    ) -> PerceptionState:
        """After a successful perception cycle.

        ``budget_multiplier`` (>1 under budget pressure) stretches the next-run
        interval so polling slows down when the token budget is tight. The
        starvation ceiling in ``_compute_next_run`` still applies, so a stretched
        source is never starved forever.
        """
        now = datetime.now(timezone.utc)
        state.consecutive_failures = 0
        state.last_error = None
        state.circuit_state = "closed"
        state.circuit_opened_at = None
        state.last_run_at = now
        state.last_event_count = event_count
        state.total_runs += 1
        state.pending_run = False

        state.effective_interval_s = self._compute_effective_interval(state)
        state.next_run_at = self._compute_next_run(state, budget_multiplier=budget_multiplier)
        await self._db.flush()
        return state

    async def record_failure(self, state: PerceptionState, error: str) -> PerceptionState:
        """After a failed perception cycle.

        Uses error classification to determine circuit breaker behaviour:
        - Permanent errors (401, revoked token) → open circuit immediately
        - Transient errors (timeout, 429, 503) → higher threshold (6 failures)
        - Unknown errors → default threshold (3 failures)
        """
        now = datetime.now(timezone.utc)
        error_class = classify_error(error)
        threshold = _threshold_for_error_class(error_class)

        state.consecutive_failures += 1
        state.last_error = error[:512]
        state.pending_run = False

        if state.consecutive_failures >= threshold:
            state.circuit_state = "open"
            state.circuit_opened_at = now
            logger.warning(
                "Circuit opened for %s/%s after %d failures (error_class=%s, threshold=%d)",
                state.user_id,
                state.source,
                state.consecutive_failures,
                error_class,
                threshold,
            )
        else:
            state.effective_interval_s = self._compute_effective_interval(state)
            state.next_run_at = self._compute_next_run(state)

        await self._db.flush()
        return state

    async def request_run(
        self,
        workspace_id: str,
        user_id: str,
        source: str,
        signal_source: str,
    ) -> PerceptionState:
        """Signal that a source should be checked soon.

        Called by: webhook delivery, user intent, agent request, scheduler.
        """
        now = datetime.now(timezone.utc)
        state = await self.get_or_create_state(workspace_id, user_id, source)

        if state.mode == "paused":
            if signal_source in self._wake_signals:
                state.mode = "active"
            else:
                return state  # don't wake paused sources without explicit activation

        state.pending_run = True
        state.signal_source = signal_source
        state.signal_at = now
        await self._db.flush()
        return state

    async def apply_agent_policy(
        self,
        state: PerceptionState,
        next_check_seconds: int | None = None,
        watch_entities: list[str] | None = None,
    ) -> PerceptionState:
        """Apply agent-informed policy within guardrails."""
        if next_check_seconds is not None:
            clamped = max(MIN_INTERVAL_S, min(next_check_seconds, MAX_INTERVAL_S))
            state.agent_interval_s = clamped

        if watch_entities is not None:
            state.watch_entities = watch_entities

        state.effective_interval_s = self._compute_effective_interval(state)
        state.next_run_at = self._compute_next_run(state)
        await self._db.flush()
        return state

    # ------------------------------------------------------------------
    # Internal computations
    # ------------------------------------------------------------------

    def _compute_effective_interval(self, state: PerceptionState) -> int:
        """Merge base, agent override, and backoff; clamp within bounds."""
        # Start from agent override if set, otherwise base
        if state.agent_interval_s is not None:
            base = state.agent_interval_s
        else:
            base = state.base_interval_s

        # Apply failure backoff: 2^failures capped at BACKOFF_CAP
        if state.consecutive_failures > 0:
            backoff = min(2**state.consecutive_failures, BACKOFF_CAP)
            base = base * backoff

        return max(MIN_INTERVAL_S, min(base, MAX_INTERVAL_S))

    def _compute_next_run(self, state: PerceptionState, budget_multiplier: int = 1) -> datetime:
        """Compute next_run_at from effective interval."""
        now = datetime.now(timezone.utc)
        # Budget pressure stretches the interval, but never past the hard
        # guardrail (clamp by construction, not by coincidence).
        interval = min(state.effective_interval_s * max(budget_multiplier, 1), MAX_INTERVAL_S)

        # Starvation prevention: if last_run_at is very old, don't push further
        if state.last_run_at is not None:
            silence = (now - state.last_run_at).total_seconds()
            if silence >= STARVATION_CEILING_S:
                return now  # run immediately

        return now + timedelta(seconds=interval)

    def _maybe_reopen_circuit(self, state: PerceptionState, now: datetime) -> None:
        """Transition open circuit to half_open after cooldown."""
        if state.circuit_state != "open" or state.circuit_opened_at is None:
            return
        elapsed = (now - state.circuit_opened_at).total_seconds()
        if elapsed >= CIRCUIT_COOLDOWN_S:
            state.circuit_state = "half_open"
            state.consecutive_failures = 0
            logger.info(
                "Circuit half-opened for %s/%s after %ds cooldown",
                state.user_id,
                state.source,
                int(elapsed),
            )
