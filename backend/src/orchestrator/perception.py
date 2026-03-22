"""Ambient perception cycle coordinator.

Manages scheduled observation cycles across data sources.
Push-first: if a source has active webhook subscriptions, skip polling.
Falls back to polling for sources without push delivery.

Each cycle: Observer -> Librarian -> Planner -> (Governor -> Presenter if needed).
"""

import logging
from datetime import datetime, timezone

from src.services.mcp_resilience import MCPCircuitBreaker

logger = logging.getLogger(__name__)

# Default observation intervals (seconds)
SOURCE_INTERVALS = {
    "gmail": 300,  # 5 minutes
    "calendar": 900,  # 15 minutes
    "slack": 300,  # 5 minutes
    "github": 600,  # 10 minutes
}


class PerceptionCoordinator:
    """Coordinates ambient perception cycles across data sources."""

    def __init__(self, orchestrator, user_id: str, workspace_id: str = ""):
        self._orchestrator = orchestrator
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._last_run: dict[str, datetime] = {}
        self._enabled_sources: set[str] = set()
        self._push_sources: set[str] = set()
        self._interval_multiplier: int = 1
        self._circuit_breaker = MCPCircuitBreaker(
            failure_threshold=3, cooldown_seconds=300.0
        )
        self._consecutive_failures: dict[str, int] = {}

    def enable_source(self, source: str) -> None:
        self._enabled_sources.add(source)

    def disable_source(self, source: str) -> None:
        self._enabled_sources.discard(source)

    def mark_push_active(self, source: str) -> None:
        """Mark a source as having active push delivery (skip polling)."""
        self._push_sources.add(source)

    def mark_push_inactive(self, source: str) -> None:
        """Mark a source as no longer receiving push delivery."""
        self._push_sources.discard(source)

    def set_push_sources(self, sources: set[str]) -> None:
        """Bulk-set which sources have active push subscriptions."""
        self._push_sources = sources

    def set_interval_multiplier(self, multiplier: int) -> None:
        """Increase intervals when budget is tight (e.g., 3x = degraded mode)."""
        self._interval_multiplier = max(1, multiplier)

    def get_due_sources(self) -> list[str]:
        """Return sources that are due for observation (polling only, not push)."""
        now = datetime.now(timezone.utc)
        due = []
        for source in self._enabled_sources:
            # Skip sources with active push delivery
            if source in self._push_sources:
                continue

            base_interval = SOURCE_INTERVALS.get(source, 600)
            # Adaptive backoff: double interval per consecutive failure, cap at 8x
            failure_count = self._consecutive_failures.get(source, 0)
            backoff = min(2 ** failure_count, 8) if failure_count > 0 else 1
            interval = base_interval * self._interval_multiplier * backoff
            last = self._last_run.get(source)
            if last is None or (now - last).total_seconds() >= interval:
                due.append(source)
        return due

    def get_push_sources(self) -> set[str]:
        """Return sources that are receiving push delivery."""
        return self._push_sources & self._enabled_sources

    async def sync_push_sources(self) -> None:
        """Sync push source set from webhook subscriptions in the database."""
        try:
            from src.integrations.sync.webhook_manager import WebhookManager

            async with self._orchestrator._db_factory() as db:
                mgr = WebhookManager(db, self._workspace_id, callback_base_url="")
                active = await mgr.get_sources_with_push()
                self._push_sources = active
                logger.info(
                    "push_sources_synced",
                    extra={"sources": list(active)},
                )
        except Exception as e:
            logger.debug("Failed to sync push sources: %s", e)

    async def refresh_enabled_sources(self) -> None:
        """Re-check connector auth and disable sources that lost authorization."""
        try:
            from src.services.scheduler import Scheduler

            async with self._orchestrator._db_factory() as db:
                authorized = await Scheduler._get_authorized_providers(db, self._user_id)
        except Exception:
            logger.debug("Failed to refresh authorized sources", exc_info=True)
            return

        stale = self._enabled_sources - authorized
        if stale:
            logger.info(
                "perception_disabling_unauthorized",
                extra={"sources": list(stale)},
            )
            self._enabled_sources -= stale

    async def run_due_cycles(self) -> list[dict]:
        """Run perception cycles for all due sources."""
        # Re-validate authorization before pulling
        await self.refresh_enabled_sources()

        due = self.get_due_sources()
        results = []

        for source in due:
            # Circuit breaker: skip sources that have failed repeatedly
            if not self._circuit_breaker.is_available(source):
                logger.info("perception_circuit_open", extra={"source": source})
                results.append({"status": "circuit_open", "source": source})
                continue

            logger.info("perception_cycle_starting", extra={"source": source})
            try:
                result = await self._orchestrator.run_perception_cycle(
                    source, user_id=self._user_id, workspace_id=self._workspace_id
                )
                self._last_run[source] = datetime.now(timezone.utc)
                results.append(result)

                if result.get("status") == "error":
                    self._circuit_breaker.record_failure(source)
                    self._consecutive_failures[source] = (
                        self._consecutive_failures.get(source, 0) + 1
                    )
                else:
                    self._circuit_breaker.record_success(source)
                    self._consecutive_failures[source] = 0

                # Emit connector.synced domain event
                await self._publish_event(
                    "connector.synced",
                    self._user_id,
                    {"source": source, "status": result.get("status", "ok")},
                )
            except Exception as e:
                logger.error(
                    "perception_cycle_failed",
                    extra={"source": source, "error": str(e)},
                )
                results.append({"status": "error", "source": source, "error": str(e)})
                self._circuit_breaker.record_failure(source)
                self._consecutive_failures[source] = (
                    self._consecutive_failures.get(source, 0) + 1
                )
                # Emit connector.error domain event
                await self._publish_event(
                    "connector.error",
                    self._user_id,
                    {"source": source, "error": str(e)[:500]},
                )

        return results

    async def _publish_event(self, event_type: str, user_id: str, payload: dict) -> None:
        """Publish a domain event via the orchestrator's event bus (best-effort)."""
        try:
            await self._orchestrator._publish_event(event_type, user_id, payload)
        except Exception:
            logger.debug("Failed to publish %s event", event_type, exc_info=True)

    async def restore_cursors(self) -> None:
        """Restore last observation times from cursor data on startup."""
        from sqlalchemy import select

        from src.models.observation_cursor import ObservationCursor

        try:
            async with self._orchestrator._db_factory() as db:
                conditions = [ObservationCursor.user_id == self._user_id]
                if self._workspace_id:
                    conditions.append(ObservationCursor.workspace_id == self._workspace_id)
                result = await db.execute(select(ObservationCursor).where(*conditions))
                cursors = result.scalars().all()
                for cursor in cursors:
                    self._last_run[cursor.source] = cursor.last_observation_at
                    logger.info(
                        "perception_cursor_restored",
                        extra={
                            "source": cursor.source,
                            "last_observation": cursor.last_observation_at.isoformat(),
                        },
                    )
        except Exception as e:
            logger.error("Failed to restore perception cursors: %s", e)

        # Also sync push sources
        await self.sync_push_sources()
