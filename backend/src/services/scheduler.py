"""SchedulerLoop — backend-owned dynamic scheduler.

Runs as an asyncio task alongside StreamConsumerManager in the worker thread.
Every POLL_INTERVAL seconds, queries Postgres for due schedules and fires them.

Perception is driven by the ``perception_state`` table: sources with
``pending_run=True`` or ``next_run_at <= now`` are picked up by
``_tick_perception()`` each cycle.
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
        """One scheduler cycle: perception, follow-ups, background tasks, schedules."""
        factory = get_session_factory()

        # 1. Drive perception from perception_state table
        await self._tick_perception(factory)

        # 2. Check follow-up notifications
        await self._check_follow_ups(factory)

        # 3. Execute pending background tasks
        await self._tick_background_tasks(factory)

        # 4. Process due schedules
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

    # ------------------------------------------------------------------
    # Perception tick — drives cycles from perception_state table
    # ------------------------------------------------------------------

    async def _tick_perception(self, factory) -> None:
        """Run perception cycles for sources with pending_run or next_run_at <= now."""
        if not self._orchestrator:
            return

        try:
            from src.services.perception_policy import PerceptionPolicyService

            budget_status = None
            budget_multiplier = 1
            try:
                async with factory() as db:
                    budget_status = await self._orchestrator._budget.get_budget_status(db)
                    if not self._orchestrator._budget.should_allow_perception(budget_status):
                        return
                    budget_multiplier = (
                        self._orchestrator._budget.get_perception_interval_multiplier(budget_status)
                    )
            except Exception:
                logger.debug("Budget check failed, proceeding with defaults", exc_info=True)

            async with factory() as db:
                svc = PerceptionPolicyService(db)
                due_states = await svc.get_due_sources_all_users(budget_multiplier)

                if not due_states:
                    return

                for state in due_states:
                    try:
                        workspace_id = await self._resolve_workspace(state.user_id)
                    except (ValueError, Exception):
                        workspace_id = state.workspace_id or ""

                    try:
                        result = await self._orchestrator.run_perception_cycle(
                            state.source,
                            user_id=state.user_id,
                            workspace_id=workspace_id,
                        )
                        event_count = result.get("events", 0)
                        if result.get("status") == "error":
                            await svc.record_failure(state, result.get("error", "unknown"))
                        else:
                            await svc.record_success(state, event_count)
                    except Exception as e:
                        await svc.record_failure(state, str(e)[:512])
                        logger.warning(
                            "Perception cycle failed for %s/%s: %s",
                            state.user_id,
                            state.source,
                            e,
                        )

                await db.commit()
                logger.info("Perception tick: %d sources processed", len(due_states))
        except Exception:
            logger.warning("Perception tick error", exc_info=True)

    # ------------------------------------------------------------------
    # Background task execution
    # ------------------------------------------------------------------

    async def _tick_background_tasks(self, factory) -> None:
        """Execute pending background tasks queued by the orchestrator."""
        if not self._orchestrator:
            return

        try:
            from src.models.task_graph import TaskRun

            async with factory() as db:
                result = await db.execute(
                    select(TaskRun)
                    .where(
                        TaskRun.status == "pending",
                        TaskRun.source == "background",
                    )
                    .order_by(TaskRun.created_at.asc())
                    .limit(3)
                )
                pending = list(result.scalars().all())

                if not pending:
                    return

                for run in pending:
                    try:
                        workspace_id = run.workspace_id or ""
                        from src.services.graph_executor import (
                            create_graph_executor,
                        )

                        executor = await create_graph_executor(
                            settings=self._settings,
                            db=db,
                            workspace_id=workspace_id,
                        )
                        completed = await executor.execute_run(
                            run.run_id,
                        )
                        logger.info(
                            "Background task %s completed: %s",
                            run.run_id,
                            completed.status,
                        )
                    except Exception as e:
                        logger.warning(
                            "Background task %s failed: %s",
                            run.run_id,
                            e,
                        )

                await db.commit()
                logger.info(
                    "Background tick: %d tasks processed",
                    len(pending),
                )
        except Exception:
            logger.warning("Background task tick error", exc_info=True)

    # ------------------------------------------------------------------
    # Follow-up notifications
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Observation source helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Schedule action dispatch
    # ------------------------------------------------------------------

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
            if not self._orchestrator:
                raise RuntimeError("Orchestrator required for observe_source")

            # Check if perception_state manages this source — if so, skip
            # (perception_state is the primary mechanism; schedules are fallback)
            try:
                from src.models.perception_state import PerceptionState

                factory = get_session_factory()
                async with factory() as db:
                    result = await db.execute(
                        select(PerceptionState).where(
                            PerceptionState.user_id == sched.user_id,
                            PerceptionState.source == source,
                            PerceptionState.mode != "paused",
                        )
                    )
                    if result.scalar_one_or_none() is not None:
                        logger.debug(
                            "observe_source skipped for %s — managed by perception_state",
                            source,
                        )
                        return
            except Exception:
                pass  # fall through to legacy path

            # Legacy fallback: gate on auth and run directly
            factory = get_session_factory()
            async with factory() as db:
                authorized = await self._get_authorized_providers(db, sched.user_id)
            if source not in authorized:
                logger.info(
                    "Skipping observe_source for %s — no active connector",
                    source,
                )
                return

            await self._orchestrator.run_perception_cycle(
                source, user_id=sched.user_id, workspace_id=workspace_id
            )
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
                ", ".join(f"{c.name}={c.status}" for c in checks),
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
        elif action == "wake_agent":
            # Agent-requested wakeup — bridge between agent decisions and perception
            agent = config.get("agent", "observer")
            source = config.get("source")
            if agent == "observer" and source:
                from src.services.perception_policy import PerceptionPolicyService

                factory = get_session_factory()
                async with factory() as db:
                    svc = PerceptionPolicyService(db)
                    await svc.request_run(
                        workspace_id=workspace_id,
                        user_id=sched.user_id,
                        source=source,
                        signal_source="agent",
                    )
                    await db.commit()
            elif self._orchestrator:
                msg = config.get("message", f"Wake {agent}")
                await self._orchestrator.process_message(
                    message=msg,
                    user_id=sched.user_id,
                    workspace_id=workspace_id,
                    surface="scheduler",
                )
        else:
            logger.warning("Unknown action_type: %s for schedule %s", action, sched.schedule_id)
