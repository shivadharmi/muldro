"""SchedulerLoop — backend-owned dynamic scheduler.

Runs as an asyncio task alongside CallbackWorker in the worker thread.
Every POLL_INTERVAL seconds, queries Postgres for due schedules and fires them.
"""

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from src.config.settings import Settings
from src.models.database import get_session_factory
from src.models.schedules import Schedule
from src.services.heartbeat import HeartbeatService
from src.services.openclaw_client import OpenClawClient

logger = logging.getLogger(__name__)


def compute_next_run(cron_expr: str, after: datetime) -> datetime:
    """Compute next fire time from cron expression using croniter."""
    return croniter(cron_expr, after).get_next(datetime)


class SchedulerLoop:
    """Backend-owned scheduler. Runs as asyncio task in worker thread."""

    POLL_INTERVAL = 30  # seconds between schedule checks

    def __init__(self, settings: Settings):
        self._settings = settings
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
        """One scheduler cycle: query due schedules, fire each, advance next_run_at."""
        factory = get_session_factory()

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
                        sched.schedule_id, sched.next_run_at,
                    )
                elif sched.next_run_at is not None and sched.next_run_at <= now:
                    due.append(sched)

            if not due:
                await db.commit()  # persist any repairs
                return

            openclaw = OpenClawClient(self._settings)

            for sched in due:
                try:
                    await self._fire(sched, openclaw)
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

    def _delivery_instruction(self) -> str:
        """Build instruction for agent to deliver results to user via Telegram."""
        chat_id = self._settings.telegram_chat_id
        if not chat_id:
            return ""
        return (
            f"\n\nIMPORTANT: After completing the task, send a concise summary to the user "
            f"using the message tool with channel=telegram and to={chat_id}. "
            f"Keep it brief and actionable."
        )

    async def _fire(self, sched: Schedule, openclaw: OpenClawClient) -> None:
        """Dispatch a single schedule's action."""
        config = sched.action_config or {}
        action = sched.action_type
        deliver = self._delivery_instruction()

        if action == "observe_source":
            source = config["source"]
            await openclaw.run_agent_turn(
                f"[SCHEDULED:observe-{source}] Check {source} for new items, "
                f"ingest important ones via jarvis_ingest_event, "
                f"then report via jarvis_report_observation."
                f"{deliver}"
            )
        elif action == "generate_briefing":
            await openclaw.run_agent_turn(
                "[SCHEDULED:briefing] Run observations, generate daily briefing "
                "via jarvis_brief, and deliver to user via message."
                f"{deliver}"
            )
        elif action == "meeting_prep":
            await openclaw.run_agent_turn(
                "[SCHEDULED:meeting-prep] Check calendar for meetings in next 30min. "
                "If found, call jarvis_meeting_prep and deliver to user."
                f"{deliver}"
            )
        elif action == "heartbeat":
            factory = get_session_factory()
            async with factory() as hb_db:
                hb = HeartbeatService(self._settings, hb_db)
                await hb.run(sched.user_id)
                await hb_db.commit()
        elif action == "custom_agent_task":
            instructions = config.get("instructions", "")
            await openclaw.run_agent_turn(
                f"[SCHEDULED:custom] {instructions}{deliver}"
            )
        elif action == "wake_agent":
            message = config.get("message", "Scheduled wake-up")
            await openclaw.wake_agent(message)
        else:
            logger.warning("Unknown action_type: %s for schedule %s", action, sched.schedule_id)
