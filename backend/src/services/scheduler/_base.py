"""SchedulerLoop base: lifecycle, the tick-cadence dispatcher, and shared
workspace/source helpers.

Holds the mutable tick state (``_tick_count``, ``_last_persona_batch_at``) that the
tick mixins read and write through the composed instance.
"""

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from src.config.settings import Settings
from src.models.database import get_session_factory
from src.models.schedules import Schedule
from src.services.workspace_resolver import resolve_workspace_id

logger = logging.getLogger(__name__)


def compute_next_run(cron_expr: str, after: datetime) -> datetime:
    """Compute next fire time from cron expression using croniter."""
    return croniter(cron_expr, after).get_next(datetime)


class SchedulerBase:
    """Lifecycle, cadence dispatch, and shared helpers for the scheduler."""

    POLL_INTERVAL = 30  # seconds between schedule checks

    def __init__(self, settings: Settings, orchestrator=None, user_ids: list[str] | None = None):
        self._settings = settings
        self._orchestrator = orchestrator
        self._user_ids = user_ids or []
        self._running = False

    async def run(self) -> None:
        """Main loop: every 30s, check for due schedules and fire them."""
        self._running = True
        logger.info("SchedulerLoop started (poll every %ds)", self.POLL_INTERVAL)

        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.warning("Scheduler tick error", exc_info=True)
            await asyncio.sleep(self.POLL_INTERVAL)

    async def stop(self) -> None:
        """Signal the scheduler to stop."""
        self._running = False

    async def _tick(self) -> None:
        """One scheduler cycle: perception, follow-ups, background tasks, eviction, schedules."""
        factory = get_session_factory()

        # 1. Drive perception from perception_state table
        await self._tick_perception(factory)

        # 2. Check follow-up notifications
        await self._check_follow_ups(factory)

        # 2b. Re-deliver pending notifications reset by _check_follow_ups
        await self._tick_pending_notifications(factory)

        # 3. Execute pending background tasks
        await self._tick_background_tasks(factory)

        # 4a. Eviction + DLQ retry — every 5th tick (~150s)
        self._tick_count = getattr(self, "_tick_count", 0) + 1
        if self._tick_count % 5 == 0:
            await self._tick_eviction(factory)
            await self._tick_dlq_retry(factory)

            # Memory expiration — cascade to Qdrant
            vector_store = None
            if self._settings.qdrant_url:
                from src.services.vector_store import VectorStore

                vector_store = VectorStore(self._settings)
            await self._tick_memory_expiration(factory, vector_store)

        # 4b. Persona batch — every 10th tick (~5 min)
        await self._tick_persona_batch()

        # 4c. Memory consolidation — once daily at ~2 AM UTC
        from datetime import datetime

        current_hour = datetime.now(timezone.utc).hour
        if self._tick_count % 120 == 0 and current_hour == 2:
            await self._tick_consolidation(factory)

            # 4c-ii. Stability refresh — sync Qdrant payloads with Postgres scores
            daily_vector_store = None
            if self._settings.qdrant_url:
                from src.services.vector_store import VectorStore

                daily_vector_store = VectorStore(self._settings)
            await self._tick_stability_refresh(factory, daily_vector_store)

        # 4d. Stuck run health check — every tick
        await self._tick_run_health_check(factory)

        # 5. Process due schedules
        async with factory() as db:
            now = datetime.now(timezone.utc)

            # Fix any enabled schedules with null next_run_at (can happen if
            # enabled via PATCH without recomputing, or from old seed data)
            from sqlalchemy import or_

            result = await db.execute(
                select(Schedule)
                .where(
                    Schedule.enabled.is_(True),
                    or_(
                        Schedule.next_run_at <= now,
                        Schedule.next_run_at.is_(None),
                    ),
                )
                .order_by(Schedule.next_run_at.asc().nullsfirst())
            )
            candidates = list(result.scalars().all())

            # Separate: schedules needing next_run_at repair vs actually due
            due = []
            for sched in candidates:
                if sched.next_run_at is None and sched.cron_expr:
                    sched.next_run_at = compute_next_run(sched.cron_expr, now)
                    logger.info(
                        "Repaired next_run_at for %s → %s",
                        sched.schedule_id,
                        sched.next_run_at,
                    )
                elif sched.next_run_at is not None and sched.next_run_at <= now:
                    due.append(sched)

            if not due:
                await db.commit()  # persist any repairs
                return

            for sched in due:
                try:
                    await self._fire(sched)
                    sched.last_run_at = now
                    sched.run_count += 1
                    sched.consecutive_failures = 0
                    sched.last_error = None
                except Exception as e:
                    sched.consecutive_failures += 1
                    sched.last_error = str(e)[:512]
                    logger.warning("Schedule %s failed: %s", sched.schedule_id, e)
                    if sched.consecutive_failures >= 5:
                        sched.enabled = False
                        logger.warning(
                            "Auto-disabled schedule %s after 5 failures", sched.schedule_id
                        )

                # Advance next_run_at
                if sched.schedule_type == "recurring" and sched.cron_expr:
                    sched.next_run_at = compute_next_run(sched.cron_expr, now)
                elif sched.schedule_type == "one_shot":
                    sched.enabled = False
                    sched.next_run_at = None

            await db.commit()
            logger.info("Scheduler tick: %d due, fired", len(due))

    async def _get_observation_sources(self, user_id: str) -> list[str]:
        """Get observation sources that are both configured AND authorized."""
        factory = get_session_factory()
        async with factory() as db:
            authorized = await self._get_authorized_providers(db, user_id)
            if not authorized:
                return []

            try:
                from src.services.settings_service import SettingsService

                svc = SettingsService(db)
                configured = await svc.get_observation_sources(user_id)
                wanted = {s["provider"] for s in configured if s.get("enabled", True)}
                return sorted(wanted & authorized)
            except Exception:
                logger.debug(
                    "Failed to load observation settings, using authorized set",
                    exc_info=True,
                )
                return sorted(authorized)

    @staticmethod
    async def _get_authorized_providers(db, user_id: str) -> set[str]:
        """Return provider names that have active auth for this user."""
        from sqlalchemy import select

        authorized: set[str] = set()

        try:
            from src.models.integration_installation import IntegrationInstallation
            from src.models.users import WorkspaceMember

            ws_result = await db.execute(
                select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)
            )
            ws_ids = [row[0] for row in ws_result.all()]
            if ws_ids:
                inst_result = await db.execute(
                    select(IntegrationInstallation.server_name).where(
                        IntegrationInstallation.workspace_id.in_(ws_ids),
                        IntegrationInstallation.status == "active",
                        IntegrationInstallation.enabled.is_(True),
                    )
                )
                authorized.update(row[0] for row in inst_result.all())
        except Exception:
            logger.debug("IntegrationInstallation lookup failed", exc_info=True)

        return authorized

    async def _resolve_workspace(self, user_id: str) -> str:
        """Resolve workspace_id for a user in background context."""
        factory = get_session_factory()
        async with factory() as db:
            return await resolve_workspace_id(db, user_id)
