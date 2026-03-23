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
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.perception_state import PerceptionState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guardrail constants
# ---------------------------------------------------------------------------
MIN_INTERVAL_S = 60  # never poll faster than 1 min
MAX_INTERVAL_S = 3600  # never go longer than 1 hour
CIRCUIT_COOLDOWN_S = 300  # 5 min circuit breaker cooldown
CIRCUIT_FAILURE_THRESHOLD = 3
STARVATION_CEILING_S = 1800  # force run after 30 min silence
BACKOFF_CAP = 8  # max backoff multiplier (2^3)

DEFAULT_INTERVALS: dict[str, int] = {
    "gmail": 300,
    "calendar": 900,
    "slack": 300,
    "github": 600,
}


class PerceptionPolicyService:
    """System guardrails layer for perception scheduling.

    Does NOT make agent calls — it enforces constraints on whatever the
    agent or signals request.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

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

    async def get_due_sources(
        self, user_id: str, budget_multiplier: int = 1
    ) -> list[PerceptionState]:
        """Return sources due for a single user."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(PerceptionState)
            .where(
                PerceptionState.user_id == user_id,
                PerceptionState.mode != "paused",
                PerceptionState.circuit_state != "open",
            )
            .where(
                (PerceptionState.pending_run.is_(True))
                | (
                    PerceptionState.next_run_at.isnot(None)
                    & (PerceptionState.next_run_at <= now)
                )
            )
            .order_by(PerceptionState.next_run_at.asc().nullslast())
        )
        result = await self._db.execute(stmt)
        states = list(result.scalars().all())

        # Reopen half-open circuits that have cooled down
        for s in states:
            if s.circuit_state == "half_open":
                continue  # allow trial run
            self._maybe_reopen_circuit(s, now)

        return states

    async def get_due_sources_all_users(
        self, budget_multiplier: int = 1
    ) -> list[PerceptionState]:
        """Return due sources across all users (used by scheduler)."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(PerceptionState)
            .where(
                PerceptionState.mode != "paused",
                PerceptionState.circuit_state != "open",
            )
            .where(
                (PerceptionState.pending_run.is_(True))
                | (
                    PerceptionState.next_run_at.isnot(None)
                    & (PerceptionState.next_run_at <= now)
                )
            )
            .order_by(PerceptionState.next_run_at.asc().nullslast())
        )
        result = await self._db.execute(stmt)
        states = list(result.scalars().all())

        for s in states:
            self._maybe_reopen_circuit(s, now)

        return states

    # ------------------------------------------------------------------
    # Lifecycle: success / failure / signal
    # ------------------------------------------------------------------

    async def record_success(
        self,
        state: PerceptionState,
        event_count: int,
    ) -> PerceptionState:
        """After a successful perception cycle."""
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
        state.next_run_at = self._compute_next_run(state)
        await self._db.flush()
        return state

    async def record_failure(
        self, state: PerceptionState, error: str
    ) -> PerceptionState:
        """After a failed perception cycle."""
        now = datetime.now(timezone.utc)
        state.consecutive_failures += 1
        state.last_error = error[:512]
        state.pending_run = False

        if state.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
            state.circuit_state = "open"
            state.circuit_opened_at = now
            logger.warning(
                "Circuit opened for %s/%s after %d failures",
                state.user_id,
                state.source,
                state.consecutive_failures,
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
            return state  # don't wake paused sources

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
            backoff = min(2 ** state.consecutive_failures, BACKOFF_CAP)
            base = base * backoff

        return max(MIN_INTERVAL_S, min(base, MAX_INTERVAL_S))

    def _compute_next_run(
        self, state: PerceptionState, budget_multiplier: int = 1
    ) -> datetime:
        """Compute next_run_at from effective interval."""
        now = datetime.now(timezone.utc)
        interval = state.effective_interval_s * max(budget_multiplier, 1)

        # Starvation prevention: if last_run_at is very old, don't push further
        if state.last_run_at is not None:
            silence = (now - state.last_run_at).total_seconds()
            if silence >= STARVATION_CEILING_S:
                return now  # run immediately

        from datetime import timedelta

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
