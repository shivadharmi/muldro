"""SchedulerLoop — backend-owned dynamic scheduler.

Runs as an asyncio task alongside StreamConsumerManager in the worker thread.
Every POLL_INTERVAL seconds, queries Postgres for due schedules and fires them.
"""

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from src.api.deps import resolve_workspace_id
from src.config.settings import Settings
from src.models.database import get_session_factory
from src.models.schedules import Schedule
from src.services.heartbeat import HeartbeatService

logger = logging.getLogger(__name__)


def compute_next_run(cron_expr: str, after: datetime) -> datetime:
    """Compute next fire time from cron expression using croniter."""
    return croniter(cron_expr, after).get_next(datetime)


class SchedulerLoop:
    """Backend-owned scheduler. Runs as asyncio task in worker thread."""

    POLL_INTERVAL = 30  # seconds between schedule checks

    def __init__(self, settings: Settings, orchestrator=None, user_ids: list[str] | None = None):
        self._settings = settings
        self._orchestrator = orchestrator
        self._user_ids = user_ids or []
        self._running = False
        self._perception: dict[str, object] = {}

    async def run(self) -> None:
        """Main loop: every 30s, check for due schedules and fire them."""
        self._running = True

        # Restore perception coordinator cursors from DB
        await self._init_perception()

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
        """One scheduler cycle: query due schedules, fire each, advance next_run_at."""
        factory = get_session_factory()

        # Check follow-up notifications
        await self._check_follow_ups(factory)

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

    async def _check_follow_ups(self, factory) -> None:
        """Re-queue notifications whose follow_up_at has passed."""
        try:
            from src.models.notifications import Notification as NotifModel

            async with factory() as db:
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(NotifModel)
                    .where(
                        NotifModel.follow_up_at <= now,
                        NotifModel.status.in_(["sent", "pending"]),
                    )
                    .limit(10)
                )
                due = result.scalars().all()
                for n in due:
                    n.follow_up_at = None
                    n.status = "pending"
                if due:
                    await db.commit()
                    logger.info("Re-queued %d follow-up notifications", len(due))
        except Exception:
            logger.debug("Follow-up check failed", exc_info=True)

    async def _init_perception(self) -> None:
        """Initialize perception coordinators per user and restore cursors."""
        if not self._orchestrator or not self._user_ids:
            return
        try:
            from src.orchestrator.perception import PerceptionCoordinator

            for uid in self._user_ids:
                coord = PerceptionCoordinator(self._orchestrator, user_id=uid)
                for source in ("gmail", "calendar", "slack", "github"):
                    coord.enable_source(source)
                await coord.restore_cursors()
                self._perception[uid] = coord
            logger.info(
                "Perception coordinators initialized for %d user(s)",
                len(self._perception),
            )
        except Exception:
            logger.warning("Perception coordinator init failed", exc_info=True)
            self._perception = {}

    async def _resolve_workspace(self, user_id: str) -> str:
        """Resolve workspace_id for a user in background context."""
        factory = get_session_factory()
        async with factory() as db:
            return await resolve_workspace_id(db, user_id)

    async def _fire(self, sched: Schedule) -> None:
        """Dispatch a single schedule's action via the orchestrator."""
        config = sched.action_config or {}
        action = sched.action_type

        # Resolve workspace_id for workspace-scoped calls
        try:
            workspace_id = await self._resolve_workspace(sched.user_id)
        except ValueError:
            workspace_id = ""

        if action == "observe_source":
            source = config["source"]
            if self._orchestrator:
                await self._orchestrator.run_perception_cycle(
                    source, user_id=sched.user_id, workspace_id=workspace_id
                )
            else:
                raise RuntimeError("Orchestrator required for observe_source")
        elif action == "generate_briefing":
            if self._orchestrator:
                await self._orchestrator.generate_briefing(
                    user_id=sched.user_id, workspace_id=workspace_id
                )
            else:
                raise RuntimeError("Orchestrator required for generate_briefing")
        elif action == "meeting_prep":
            if self._orchestrator:
                await self._orchestrator.process_message(
                    message="Check calendar for meetings in next 30min. "
                    "If found, generate meeting prep and deliver to user.",
                    user_id=sched.user_id,
                    workspace_id=workspace_id,
                    surface="scheduler",
                )
            else:
                raise RuntimeError("Orchestrator required for meeting_prep")
        elif action == "heartbeat":
            factory = get_session_factory()
            async with factory() as hb_db:
                hb = HeartbeatService(self._settings, hb_db)
                await hb.run(sched.user_id)
                await hb_db.commit()
        elif action == "check_slos":
            from src.services.alerting import AlertingService
            from src.services.trace_store import TraceStore

            trace_store = TraceStore(elasticsearch_url=self._settings.elasticsearch_url)
            alerting = AlertingService(trace_store=trace_store)
            checks = await alerting.check_all_slos()
            logger.info(
                "SLO check complete: %s",
                {c.name: c.status for c in checks},
            )
        elif action == "consolidate_memories":
            user_id = sched.user_id
            factory = get_session_factory()
            async with factory() as db:
                from src.services.memory_service import MemoryService

                ms = MemoryService(settings=self._settings, db=db)
                merged = await ms.consolidate_memories(user_id, workspace_id=workspace_id)
                await db.commit()
                logger.info("Memory consolidation for %s: %d merged", user_id, merged)
        elif action == "evaluate_time_triggers":
            user_id = sched.user_id
            factory = get_session_factory()
            async with factory() as db:
                from src.services.watcher_service import WatcherService

                ws = WatcherService(db=db)
                insights = await ws._evaluate_time_triggers(user_id)
                await db.commit()
                if insights:
                    logger.info("Time triggers for %s: %d fired", user_id, len(insights))
        elif action == "run_watchers":
            user_id = sched.user_id
            factory = get_session_factory()
            async with factory() as db:
                from src.services.watcher_service import WatcherService

                ws = WatcherService(db=db)
                insights = await ws.run_all_watchers(user_id)
                await db.commit()
                if insights:
                    logger.info("Watchers for %s: %d insights", user_id, len(insights))
        elif action == "custom_agent_task":
            instructions = config.get("instructions", "")
            if self._orchestrator:
                await self._orchestrator.process_message(
                    message=instructions,
                    user_id=sched.user_id,
                    workspace_id=workspace_id,
                    surface="scheduler",
                )
            else:
                raise RuntimeError("Orchestrator required for custom_agent_task")
        else:
            logger.warning("Unknown action_type: %s for schedule %s", action, sched.schedule_id)
