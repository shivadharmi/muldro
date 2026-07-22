"""SchedulerLoop base: lifecycle, the tick-cadence dispatcher, and shared
workspace/source helpers.

Holds the mutable tick state (``_tick_count``, ``_last_persona_batch_at``) that the
tick mixins read and write through the composed instance.
"""

import asyncio
import logging
from datetime import datetime, timezone

from croniter import CroniterBadCronError, croniter
from sqlalchemy import select

from src.config.settings import Settings
from src.models.database import get_session_factory
from src.models.schedules import Schedule
from src.services.workspace_resolver import resolve_workspace_id

logger = logging.getLogger(__name__)


def compute_next_run(cron_expr: str, after: datetime) -> datetime:
    """Compute next fire time from cron expression using croniter."""
    return croniter(cron_expr, after).get_next(datetime)


def is_valid_cron(cron_expr: str) -> bool:
    """True if ``cron_expr`` is a well-formed croniter expression.

    Used to validate cron input at write boundaries (e.g. the schedule_reminder
    tool) so a malformed expression is rejected before it is persisted rather
    than crashing the dispatch sweep on a later tick.
    """
    return bool(cron_expr) and croniter.is_valid(cron_expr)


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

    async def _subtick_timeout(self) -> float:
        """Per-sub-tick wall-clock budget. Each sub-tick uses its own DB
        session, so a timed-out tick's session is torn down by its own
        ``async with`` context — nothing leaks across the boundary."""
        return float(getattr(self._settings, "scheduler_subtick_timeout_s", 90.0))

    async def _run_subtick(self, name: str, coro) -> bool:
        """Run a single sub-tick under a wall-clock timeout.

        A hung sub-tick (e.g. perception blocked on a leaked ``idle in
        transaction`` row lock) must never starve later sub-ticks — above all
        the approval-resume and health ticks that are the recovery path. On
        timeout we log and return False so the dispatcher CONTINUES to the next
        sub-tick instead of awaiting forever (head-of-line blocking).
        """
        timeout = await self._subtick_timeout()
        try:
            await asyncio.wait_for(coro, timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "Scheduler sub-tick %s exceeded %.0fs budget — skipping this cycle "
                "(later sub-ticks still run)",
                name,
                timeout,
            )
            return False
        except Exception:
            logger.warning("Scheduler sub-tick %s errored", name, exc_info=True)
            return False

    async def _tick(self) -> None:
        """One scheduler cycle: perception, follow-ups, background tasks, eviction, schedules.

        Each sub-tick is wrapped in a per-tick timeout so a single hung tick
        cannot freeze the whole loop (head-of-line blocking). Sub-ticks each
        open their own DB session, so a timed-out tick leaks no session.
        """
        factory = get_session_factory()

        # 1. Drive perception from perception_state table
        await self._run_subtick("perception", self._tick_perception(factory))

        # 2. Check follow-up notifications
        await self._run_subtick("follow_ups", self._check_follow_ups(factory))

        # 2b. Re-deliver pending notifications reset by _check_follow_ups
        await self._run_subtick("pending_notifications", self._tick_pending_notifications(factory))

        # 3. Execute pending background tasks (approval-resume recovery path)
        await self._run_subtick("background_tasks", self._tick_background_tasks(factory))

        # 4a. Eviction + DLQ retry — every 5th tick (~150s)
        self._tick_count = getattr(self, "_tick_count", 0) + 1
        if self._tick_count % 5 == 0:
            await self._run_subtick("eviction", self._tick_eviction(factory))
            await self._run_subtick("dlq_retry", self._tick_dlq_retry(factory))
            await self._run_subtick(
                "deferred_verification", self._tick_deferred_verification(factory)
            )

            # Memory expiration — cascade to Qdrant
            vector_store = None
            if self._settings.qdrant_url:
                from src.services.vector_store import VectorStore

                vector_store = VectorStore(self._settings)
            await self._run_subtick(
                "memory_expiration", self._tick_memory_expiration(factory, vector_store)
            )

        # 4b. Persona batch — every 10th tick (~5 min)
        await self._run_subtick("persona_batch", self._tick_persona_batch())

        # 4c. Memory consolidation — once daily at ~2 AM UTC
        from datetime import datetime

        current_hour = datetime.now(timezone.utc).hour
        if self._tick_count % 120 == 0 and current_hour == 2:
            await self._run_subtick("consolidation", self._tick_consolidation(factory))

            # 4c-ii. Stability refresh — sync Qdrant payloads with Postgres scores
            daily_vector_store = None
            if self._settings.qdrant_url:
                from src.services.vector_store import VectorStore

                daily_vector_store = VectorStore(self._settings)
            await self._run_subtick(
                "stability_refresh",
                self._tick_stability_refresh(factory, daily_vector_store),
            )

        # 4c-iii. Re-auth recovery — every 10th tick (~5 min). Self-healing
        # backstop that un-pauses perception sources paused-for-reauth once their
        # provider token is valid again (covers a missed OAuth-callback resume).
        if self._tick_count % 10 == 0:
            await self._run_subtick("reauth_recovery", self._tick_reauth_recovery(factory))

        # 4d. Stuck run health check — every tick (resume reaper lives here)
        await self._run_subtick("run_health_check", self._tick_run_health_check(factory))

        # 4e. Webhook push-channel renewal — every 120th tick (~1h).
        # No-op unless settings.webhooks_configured (poll-only default).
        if self._tick_count % 120 == 0:
            await self._run_subtick("webhook_renewal", self._tick_webhook_renewal(factory))

        # 4f. Durable-checkpoint retention sweep — every 120th tick (~1h).
        # No-op unless runtime="deep" AND a durable saver is reachable (Step 6C CF-4).
        if self._tick_count % 120 == 0:
            await self._run_subtick("checkpoint_reaper", self._tick_checkpoint_reaper(factory))

        # 5. Process due schedules (bounded so a stuck schedule fire can't hang the loop)
        await self._run_subtick("schedule_dispatch", self._process_due_schedules(factory))

    def _disable_invalid_cron(self, sched, err: Exception) -> None:
        """Isolate a schedule whose cron_expr can't be parsed.

        Disables the row and records the error so a single malformed cron
        expression (e.g. an unvalidated agent- or API-supplied value) can't abort
        the whole dispatch sweep on every tick. Mutations are persisted by the
        caller's per-schedule / end-of-sweep commit.
        """
        sched.enabled = False
        sched.next_run_at = None
        sched.consecutive_failures = (sched.consecutive_failures or 0) + 1
        sched.last_error = f"invalid cron_expr {sched.cron_expr!r}: {err}"[:512]
        logger.warning(
            "Disabled schedule %s with invalid cron_expr %r: %s",
            sched.schedule_id,
            sched.cron_expr,
            err,
        )

    async def _process_due_schedules(self, factory) -> None:
        """Fire any schedules that are due; repair null next_run_at."""
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
                    try:
                        sched.next_run_at = compute_next_run(sched.cron_expr, now)
                    except (CroniterBadCronError, ValueError) as e:
                        # A malformed cron_expr for ONE schedule must not abort the
                        # whole sweep (compute_next_run is outside the per-schedule
                        # fire try/except). Isolate and disable the poison row.
                        self._disable_invalid_cron(sched, e)
                        continue
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

            fired = 0
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
                    try:
                        sched.next_run_at = compute_next_run(sched.cron_expr, now)
                    except (CroniterBadCronError, ValueError) as e:
                        # Same isolation as the repair path: a bad cron here would
                        # otherwise blow the sub-tick after the schedule already fired.
                        self._disable_invalid_cron(sched, e)
                elif sched.schedule_type == "one_shot":
                    sched.enabled = False
                    sched.next_run_at = None

                # Commit PER-SCHEDULE so an already-fired schedule's next_run_at
                # advance is durable even if a LATER fire blows the sub-tick
                # budget. _run_subtick wraps this coroutine in asyncio.wait_for;
                # a timeout CANCELS it. A batch-wide commit-after-loop would lose
                # every advance on cancellation, leaving all schedules perpetually
                # "due" and re-firing every cycle (the per-minute briefing bug).
                await db.commit()
                fired += 1

            logger.info("Scheduler tick: %d due, %d fired", len(due), fired)

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
