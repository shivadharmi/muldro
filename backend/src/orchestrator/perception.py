"""Ambient perception cycle coordinator.

Manages scheduled observation cycles across data sources.
Each cycle: Observer -> Librarian -> Planner -> (Governor -> Presenter if needed).
"""

import logging
from datetime import datetime, timezone

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
        self._interval_multiplier: int = 1

    def enable_source(self, source: str) -> None:
        self._enabled_sources.add(source)

    def disable_source(self, source: str) -> None:
        self._enabled_sources.discard(source)

    def set_interval_multiplier(self, multiplier: int) -> None:
        """Increase intervals when budget is tight (e.g., 3x = degraded mode)."""
        self._interval_multiplier = max(1, multiplier)

    def get_due_sources(self) -> list[str]:
        """Return sources that are due for observation."""
        now = datetime.now(timezone.utc)
        due = []
        for source in self._enabled_sources:
            base_interval = SOURCE_INTERVALS.get(source, 600)
            interval = base_interval * self._interval_multiplier
            last = self._last_run.get(source)
            if last is None or (now - last).total_seconds() >= interval:
                due.append(source)
        return due

    async def run_due_cycles(self) -> list[dict]:
        """Run perception cycles for all due sources."""
        due = self.get_due_sources()
        results = []

        for source in due:
            logger.info("perception_cycle_starting", extra={"source": source})
            try:
                result = await self._orchestrator.run_perception_cycle(
                    source, user_id=self._user_id
                )
                self._last_run[source] = datetime.now(timezone.utc)
                results.append(result)
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
