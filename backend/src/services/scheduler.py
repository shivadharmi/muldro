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
        """One scheduler cycle: perception, follow-ups, background tasks, eviction, schedules."""
        factory = get_session_factory()

        # 1. Drive perception from perception_state table
        await self._tick_perception(factory)

        # 2. Check follow-up notifications
        await self._check_follow_ups(factory)

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

                # Rate limit: cap perception cycles per tick to avoid
                # API exhaustion when many sources are due simultaneously
                max_per_tick = self._settings.max_perception_per_tick
                if len(due_states) > max_per_tick:
                    # Priority: pending_run=True first (explicit user/agent requests),
                    # then by next_run_at ascending (oldest first)
                    due_states.sort(
                        key=lambda s: (not s.pending_run, s.next_run_at or datetime.max)
                    )
                    logger.info(
                        "Perception throttled: %d due, processing %d, deferring %d",
                        len(due_states),
                        max_per_tick,
                        len(due_states) - max_per_tick,
                    )
                    due_states = due_states[:max_per_tick]

                # C4: Clear pending_run BEFORE running cycles to prevent
                # the next 30s tick from double-picking the same sources
                for state in due_states:
                    state.pending_run = False
                await db.flush()

                concurrency = getattr(self._settings, "perception_concurrency", None)
                if not isinstance(concurrency, int) or concurrency < 1:
                    concurrency = 3
                perception_semaphore = asyncio.Semaphore(concurrency)

                async def _run_one(state):
                    async with perception_semaphore:
                        try:
                            ws_id = await self._resolve_workspace(state.user_id)
                        except (ValueError, Exception):
                            ws_id = state.workspace_id or ""

                        try:
                            result = await self._orchestrator.run_perception_cycle(
                                state.source,
                                user_id=state.user_id,
                                workspace_id=ws_id,
                            )
                            event_count = result.get("events", 0)
                            if result.get("status") == "error":
                                await svc.record_failure(state, result.get("error", "unknown"))
                            else:
                                await svc.record_success(state, event_count)
                            return state.source, event_count
                        except Exception as e:
                            await svc.record_failure(state, str(e)[:512])
                            logger.warning(
                                "Perception cycle failed for %s/%s: %s",
                                state.user_id,
                                state.source,
                                e,
                            )
                            return state.source, 0

                results = await asyncio.gather(
                    *(_run_one(s) for s in due_states),
                    return_exceptions=True,
                )

                for i, r in enumerate(results):
                    if isinstance(r, BaseException):
                        logger.warning(
                            "Perception gather exception for %s: %s",
                            due_states[i].source if i < len(due_states) else "unknown",
                            r,
                        )

                await db.commit()
                logger.info("Perception tick: %d sources processed", len(due_states))

                # D2: Cross-source synthesis — trigger on signal volume
                # (2+ sources with events AND 3+ total events)
                source_event_counts = {}
                for i, r in enumerate(results):
                    if not isinstance(r, BaseException):
                        src_name, evt_count = r
                        if evt_count > 0:
                            source_event_counts[src_name] = evt_count

                sources_with_events = len(source_event_counts)
                total_event_count = sum(source_event_counts.values())

                if sources_with_events >= 2 and total_event_count >= 3 and self._orchestrator:
                    try:
                        user_id = due_states[0].user_id
                        # Resolve workspace_id with fallback
                        ws_id = ""
                        for s in due_states:
                            if s.workspace_id:
                                ws_id = s.workspace_id
                                break
                        if not ws_id:
                            try:
                                ws_id = await self._resolve_workspace(user_id)
                            except Exception:
                                logger.warning(
                                    "No workspace_id for cross-source synthesis, skipping"
                                )
                                ws_id = ""
                        source_names = [s.source for s in due_states]
                        if ws_id:
                            await self._orchestrator.run_cross_source_synthesis(
                                source_names=source_names,
                                user_id=user_id,
                                workspace_id=ws_id,
                            )
                            logger.info(
                                "Cross-source synthesis triggered for %d sources",
                                sources_with_events,
                            )
                    except Exception:
                        logger.debug("Cross-source synthesis failed", exc_info=True)
        except Exception:
            logger.warning("Perception tick error", exc_info=True)

    # ------------------------------------------------------------------
    # Background task execution
    # ------------------------------------------------------------------

    async def _tick_background_tasks(self, factory) -> None:
        """Execute pending background tasks queued by the orchestrator.

        Picks up TaskRuns with source in ("background", "approval_resume")
        and status="pending". Failed tasks are retried up to max_retries,
        then moved to the dead-letter queue.
        """
        if not self._orchestrator:
            return

        try:
            from src.models.task_graph import TaskRun, TaskStep
            from src.services.execution_state import transition_run

            async with factory() as db:
                result = await db.execute(
                    select(TaskRun)
                    .where(
                        TaskRun.status == "pending",
                        TaskRun.source.in_(["background", "approval_resume"]),
                    )
                    .order_by(TaskRun.created_at.asc())
                    .limit(3)
                )
                pending = list(result.scalars().all())

                if not pending:
                    return

                for run in pending:
                    # Capture IDs before execution — if the session enters
                    # PendingRollbackError state, lazy attribute access fails.
                    run_id = run.run_id
                    plan_id = run.plan_id
                    user_id = run.user_id
                    ws_id = run.workspace_id or ""

                    try:
                        from src.services.graph_executor import (
                            create_graph_executor,
                        )

                        executor = await create_graph_executor(
                            settings=self._settings,
                            db=db,
                            workspace_id=ws_id,
                            db_factory=factory,
                            execute_tool_fn=self._orchestrator._execute_tool,
                            budget=self._orchestrator._budget,
                            circuit_breaker=getattr(self._orchestrator, "_circuit_breaker", None),
                        )

                        # Ensure steps exist before execution (defensive)
                        step_check = await db.execute(
                            select(TaskStep.step_id).where(TaskStep.run_id == run_id).limit(1)
                        )
                        if not step_check.scalar_one_or_none() and plan_id:
                            await executor.populate_run_steps(run_id, plan_id)
                            await db.flush()

                        completed = await executor.execute_run(run_id)
                        logger.info(
                            "Background task %s completed: %s",
                            run_id,
                            completed.status,
                        )
                    except Exception as e:
                        # Rollback poisoned session before any further DB access
                        await db.rollback()

                        logger.warning(
                            "Background task %s failed: %s",
                            run_id,
                            e,
                        )
                        run.retry_count = (run.retry_count or 0) + 1
                        max_retries = run.max_retries or 3

                        if run.retry_count >= max_retries:
                            # Exhausted retries — mark failed and DLQ
                            try:
                                transition_run(run, "failed")
                            except Exception:
                                run.status = "failed"
                            run.error = {
                                "type": type(e).__name__,
                                "message": str(e)[:500],
                            }
                            run.completed_at = datetime.now(timezone.utc)
                            try:
                                from src.services.dead_letter import (
                                    DeadLetterService,
                                )

                                dlq = DeadLetterService(db)
                                await dlq.enqueue(
                                    user_id=user_id,
                                    operation_type="background_task",
                                    error_type=type(e).__name__,
                                    error_message=str(e),
                                    source_id=run_id,
                                    payload={
                                        "plan_id": plan_id,
                                        "run_id": run_id,
                                    },
                                    workspace_id=ws_id,
                                )
                            except Exception:
                                logger.debug(
                                    "DLQ enqueue failed for run %s",
                                    run_id,
                                    exc_info=True,
                                )
                        else:
                            # Retry: transition back to pending
                            if run.status not in ("pending", "failed"):
                                try:
                                    transition_run(run, "failed")
                                except Exception:
                                    run.status = "failed"
                            try:
                                transition_run(run, "pending")
                            except Exception:
                                run.status = "pending"
                            logger.info(
                                "Background task %s retry %d/%d",
                                run_id,
                                run.retry_count,
                                max_retries,
                            )

                await db.commit()
                logger.info(
                    "Background tick: %d tasks processed",
                    len(pending),
                )
        except Exception:
            logger.warning("Background task tick error", exc_info=True)

    # ------------------------------------------------------------------
    # Data eviction — hard-delete expired records
    # ------------------------------------------------------------------

    async def _tick_eviction(self, factory) -> None:
        """Run eviction pass to hard-delete expired data with cascade cleanup."""
        try:
            async with factory() as db:
                from src.services.eviction_service import EvictionService

                vector_store = None
                graph_engine = None

                if self._settings.qdrant_url:
                    from src.services.vector_store import VectorStore

                    vector_store = VectorStore(self._settings)
                    await vector_store.ensure_collections()

                if self._settings.neo4j_url:
                    from src.services.graph_engine import GraphEngine

                    graph_engine = GraphEngine(self._settings)

                svc = EvictionService(
                    settings=self._settings,
                    db=db,
                    vector_store=vector_store,
                    graph_engine=graph_engine,
                )
                await svc.run_full_eviction()
                await db.commit()

                if graph_engine:
                    await graph_engine.close()
        except Exception:
            logger.warning("Eviction tick error", exc_info=True)

    # ------------------------------------------------------------------
    # DLQ retry — process dead-letter entries that can be retried
    # ------------------------------------------------------------------

    async def _tick_dlq_retry(self, factory) -> None:
        """Retry DLQ entries that haven't exceeded max attempts."""
        try:
            async with factory() as db:
                from src.services.dead_letter import DeadLetterService

                dlq = DeadLetterService(db)
                for uid in self._user_ids:
                    pending = await dlq.list_pending(uid, limit=10)
                    for entry in pending:
                        if not await dlq.mark_retrying(entry.entry_id):
                            logger.info(
                                "DLQ entry %s exhausted, marked as exhausted",
                                entry.entry_id,
                            )
                        else:
                            logger.debug(
                                "DLQ entry %s marked for retry (attempt %d)",
                                entry.entry_id,
                                entry.attempt_count,
                            )
                    await db.commit()
        except Exception:
            logger.debug("DLQ retry tick failed", exc_info=True)

    # ------------------------------------------------------------------
    # Memory expiration
    # ------------------------------------------------------------------

    async def _tick_memory_expiration(self, factory, vector_store=None) -> None:
        """Mark expired memories and cascade delete from Qdrant."""
        try:
            from sqlalchemy import func, select, text

            from src.models.memory import Memory

            async with factory() as db:
                result = await db.execute(
                    select(Memory)
                    .where(
                        Memory.status == "active",
                        Memory.ttl_days.isnot(None),
                        Memory.created_at
                        + func.cast(func.concat(Memory.ttl_days, " days"), type_=text("interval"))
                        < func.now(),
                    )
                    .limit(100)
                )
                expired = list(result.scalars())

                if not expired:
                    return

                for mem in expired:
                    mem.status = "expired"
                    if vector_store:
                        try:
                            await vector_store.delete("memories", mem.memory_id)
                        except Exception:
                            logger.debug(
                                "Qdrant delete failed for %s",
                                mem.memory_id,
                                exc_info=True,
                            )

                await db.commit()
                logger.info("Memory expiration: %d memories expired", len(expired))
        except Exception:
            logger.warning("Memory expiration tick error", exc_info=True)

    # Persona batch
    # ------------------------------------------------------------------

    async def _tick_persona_batch(self, factory=None) -> None:
        """Run Persona agent on recent interactions every 10th tick (~5 min).

        Only fires when there are 5+ interactions since last batch.
        """
        if getattr(self, "_tick_count", 0) % 10 != 0:
            return
        if not self._orchestrator:
            return

        try:
            factory = factory or get_session_factory()
            async with factory() as db:
                from sqlalchemy import select

                from src.models.interaction_log import InteractionLog

                last_batch = getattr(self, "_last_persona_batch_at", None)
                query = select(InteractionLog).order_by(InteractionLog.created_at.desc()).limit(20)
                if last_batch:
                    query = query.where(InteractionLog.created_at > last_batch)

                result = await db.execute(query)
                interactions = result.scalars().all()

                if len(interactions) < 5:
                    return

                summary = "\n".join(
                    f"- {i.message_preview or '(no preview)'} → {i.intent or 'unknown'}"
                    for i in interactions
                )
                user_id = interactions[0].user_id
                workspace_id = getattr(interactions[0], "workspace_id", "") or ""

                await self._orchestrator._call_agent(
                    "persona",
                    message=(
                        "Analyze these recent user interactions and extract"
                        f" preference patterns:\n{summary}"
                    ),
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
                self._last_persona_batch_at = datetime.now(timezone.utc)
                logger.info("Persona batch completed: %d interactions analyzed", len(interactions))

        except Exception:
            logger.warning("Persona batch tick failed", exc_info=True)

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

            trace_store = TraceStore()
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
            agent = config.get("agent", "perceiver")
            source = config.get("source")
            if agent == "perceiver" and source:
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
