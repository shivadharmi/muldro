"""Stuck-run detection and expired-approval cancellation."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)


class RunHealthTickMixin:
    """Times out stuck runs and cancels expired-approval runs."""

    async def _tick_run_health_check(self, factory) -> None:
        """Detect and remediate stuck runs. Called every scheduler tick."""
        try:
            from src.models.approvals import Approval
            from src.models.task_graph import TaskCheckpoint, TaskRun
            from src.services.execution_state import transition_run

            cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)

            async with factory() as db:
                # 1. Stuck "running" runs — updated_at older than the cutoff
                result = await db.execute(
                    select(TaskRun).where(
                        TaskRun.status == "running",
                        TaskRun.updated_at < cutoff,
                    )
                )
                stuck_runs = list(result.scalars().all())
                remediated = 0

                for run in stuck_runs:
                    # Check latest checkpoint — if recent, give a grace period
                    cp_result = await db.execute(
                        select(TaskCheckpoint)
                        .where(TaskCheckpoint.run_id == run.run_id)
                        .order_by(TaskCheckpoint.created_at.desc())
                        .limit(1)
                    )
                    latest_cp = cp_result.scalar_one_or_none()
                    if latest_cp and latest_cp.created_at > cutoff:
                        continue

                    logger.warning(
                        "Stuck run detected: %s (status=%s, last_update=%s)",
                        run.run_id,
                        run.status,
                        run.updated_at,
                    )
                    try:
                        transition_run(run, "timed_out")
                        run.error = {"message": "Run stuck — no progress for 15 minutes"}
                        run.completed_at = datetime.now(timezone.utc)
                        remediated += 1
                    except Exception:
                        run.status = "timed_out"

                # 2. Stuck "awaiting_approval" runs whose linked approval has expired.
                # Skip runs tagged "approval_resume" — those are approved and
                # waiting for the scheduler to resume them.
                result = await db.execute(
                    select(TaskRun).where(
                        TaskRun.status == "awaiting_approval",
                        TaskRun.source != "approval_resume",
                    )
                )
                awaiting_runs = list(result.scalars().all())
                expired_cancelled = 0

                for run in awaiting_runs:
                    apr_result = await db.execute(
                        select(Approval).where(
                            Approval.execution_id == run.run_id,
                            Approval.status == "expired",
                        )
                    )
                    if apr_result.scalar_one_or_none():
                        logger.warning("Cancelling run %s — approval expired", run.run_id)
                        try:
                            transition_run(run, "cancelled")
                            run.completed_at = datetime.now(timezone.utc)
                            expired_cancelled += 1
                        except Exception:
                            run.status = "cancelled"

                await db.commit()

                # Refresh global loop gauges for /metrics after remediation.
                await self._update_loop_gauges(db)
                await self._update_budget_gauges(db)

                if remediated or expired_cancelled:
                    logger.info(
                        "Health check: %d stuck runs timed out, %d expired-approval runs cancelled",
                        remediated,
                        expired_cancelled,
                    )
        except Exception:
            logger.warning("Run health check failed", exc_info=True)

        # Resume reaper — the ONLY recovery path for runs the user already
        # approved (source='approval_resume') that the background tick never
        # resumed. The block above deliberately skips those; this drives them.
        await self._reap_stale_resume_runs(factory)

        await self._reap_idle_mcp_sessions()

    async def _reap_stale_resume_runs(self, factory) -> None:
        """Re-drive (or fail) stale approval-resume runs.

        A run with ``status='awaiting_approval' AND source='approval_resume'``
        was already approved by the user; the background tick should have
        resumed it. If it has sat untouched past the stale threshold, the
        scheduler's normal resume path has failed it (e.g. a head-of-line
        blocked perception tick froze the loop). This reaper re-drives it
        through the SAME idempotent ``resume_run`` path the background tick
        uses (completed steps are skipped, so re-driving cannot double-execute).

        Bounded: each attempt increments ``retry_count``; after
        ``resume_reaper_max_attempts`` the run is transitioned to ``failed``
        with a clear error and dead-lettered, rather than hot-looping forever.

        IMPORTANT: a run paused at a NEW gate after a prior resume has
        ``source='background'`` (set by the background tick) and is genuinely
        awaiting fresh user approval — the ``source='approval_resume'`` filter
        below excludes those, so we never auto-approve a new gate.
        """
        if not self._orchestrator:
            return

        try:
            from src.models.task_graph import TaskRun
            from src.services.execution_state import transition_run

            stale_after = float(getattr(self._settings, "resume_reaper_stale_after_s", 300.0))
            max_attempts = int(getattr(self._settings, "resume_reaper_max_attempts", 5))
            batch_limit = int(getattr(self._settings, "resume_reaper_batch_limit", 5))
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after)

            async with factory() as db:
                # Bounded + lock-safe: FOR UPDATE SKIP LOCKED prevents a second
                # scheduler from double-driving the same run, and .limit() keeps
                # one pass from starving the 90s sub-tick timeout. The gauge query
                # in _update_loop_gauges stays unbounded (reports the full backlog).
                result = await db.execute(
                    select(TaskRun)
                    .where(
                        TaskRun.status == "awaiting_approval",
                        TaskRun.source == "approval_resume",
                        TaskRun.updated_at < cutoff,
                    )
                    .order_by(TaskRun.created_at.asc())
                    .limit(batch_limit)
                    .with_for_update(skip_locked=True)
                )
                stale_runs = list(result.scalars().all())
                if not stale_runs:
                    return

                redriven = 0
                failed = 0
                # Per-run transaction isolation: each run's outcome is committed
                # independently before moving on, so a LATER run raising (and its
                # rollback) can never discard an EARLIER run's durably-staged
                # failed+DLQ or successful resume.
                for run in stale_runs:
                    run_id = run.run_id
                    ws_id = run.workspace_id or ""
                    attempts = (run.retry_count or 0) + 1

                    if attempts > max_attempts:
                        # Exhausted — fail loud + DLQ instead of hot-looping.
                        logger.error(
                            "Stale approval-resume run %s exceeded %d reaper "
                            "attempts — marking failed",
                            run_id,
                            max_attempts,
                        )
                        try:
                            transition_run(run, "failed")
                        except Exception:
                            run.status = "failed"
                        run.error = {
                            "type": "resume_reaper_exhausted",
                            "message": (
                                "Approved run could not be resumed after "
                                f"{max_attempts} reaper attempts"
                            ),
                        }
                        run.completed_at = datetime.now(timezone.utc)
                        await self._dlq_stale_resume(db, run_id, ws_id, run.user_id)
                        # Commit this run's failed+DLQ NOW so a later run's
                        # failure/rollback cannot discard it.
                        try:
                            await db.commit()
                            failed += 1
                        except Exception:
                            logger.warning(
                                "Resume reaper could not commit exhausted run %s",
                                run_id,
                                exc_info=True,
                            )
                            await db.rollback()
                        continue

                    run.retry_count = attempts
                    try:
                        from src.services.graph_executor import create_graph_executor

                        executor = await create_graph_executor(
                            settings=self._settings,
                            db=db,
                            workspace_id=ws_id,
                            db_factory=factory,
                            execute_tool_fn=self._orchestrator._execute_tool,
                            budget=self._orchestrator._budget,
                            circuit_breaker=getattr(self._orchestrator, "_circuit_breaker", None),
                        )
                        # Match the background tick: reset source so a retry
                        # (failed → pending) goes through execute_run, and so a
                        # second reaper pass doesn't re-pick a run already being
                        # driven.
                        run.source = "background"
                        await db.flush()
                        completed = await executor.resume_run(run_id)
                        # resume_run commits internally; commit again to durably
                        # seal any post-resume bookkeeping before the next run.
                        await db.commit()
                        logger.warning(
                            "Resume reaper re-drove stale run %s (attempt %d): %s",
                            run_id,
                            attempts,
                            completed.status,
                        )
                        redriven += 1
                    except Exception:
                        logger.warning(
                            "Resume reaper failed to re-drive run %s (attempt %d)",
                            run_id,
                            attempts,
                            exc_info=True,
                        )
                        # Rollback only affects THIS run's uncommitted work —
                        # prior runs were already committed above.
                        await db.rollback()
                        # Re-fetch to record the bumped attempt count safely, and
                        # commit so the bumped count + restored source survive.
                        refetched = await db.get(TaskRun, run_id)
                        if refetched is not None:
                            refetched.retry_count = attempts
                            # Restore source so the run stays eligible for the
                            # next reaper pass (background tick set it to 'background').
                            refetched.source = "approval_resume"
                            try:
                                await db.commit()
                            except Exception:
                                logger.warning(
                                    "Resume reaper could not persist retry bump "
                                    "for run %s",
                                    run_id,
                                    exc_info=True,
                                )
                                await db.rollback()

                if redriven or failed:
                    logger.info(
                        "Resume reaper: %d re-driven, %d failed (exhausted)",
                        redriven,
                        failed,
                    )
        except Exception:
            logger.warning("Resume reaper failed", exc_info=True)

    async def _dlq_stale_resume(self, db, run_id: str, ws_id: str, user_id) -> None:
        """Best-effort dead-letter for an exhausted stale-resume run."""
        try:
            from src.services.dead_letter import DeadLetterService

            dlq = DeadLetterService(db)
            await dlq.enqueue(
                user_id=user_id,
                operation_type="resume_reaper",
                error_type="resume_reaper_exhausted",
                error_message=f"Approved run {run_id} unresumable after reaper attempts",
                source_id=run_id,
                payload={"run_id": run_id},
                workspace_id=ws_id,
            )
        except Exception:
            logger.debug("DLQ enqueue failed for stale resume run %s", run_id, exc_info=True)

    async def _update_loop_gauges(self, db) -> None:
        """Refresh global loop gauges (active runs, pending approvals) for
        /metrics. Best-effort — never break the health tick on a metrics error."""
        try:
            from sqlalchemy import func

            from src.models.approvals import Approval
            from src.models.task_graph import TaskRun
            from src.services.metrics_service import MetricsService

            running = await db.execute(
                select(func.count()).select_from(TaskRun).where(TaskRun.status == "running")
            )
            pending = await db.execute(
                select(func.count()).select_from(Approval).where(Approval.status == "pending")
            )
            MetricsService.set_active_runs(running.scalar() or 0)
            MetricsService.set_pending_approvals(pending.scalar() or 0)

            # Stuck-resume backlog: runs the user approved but that never
            # resumed past the stale threshold — the gap the reaper covers.
            stale_after = float(getattr(self._settings, "resume_reaper_stale_after_s", 300.0))
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after)
            stuck = await db.execute(
                select(func.count())
                .select_from(TaskRun)
                .where(
                    TaskRun.status == "awaiting_approval",
                    TaskRun.source == "approval_resume",
                    TaskRun.updated_at < cutoff,
                )
            )
            MetricsService.set_stuck_resume_runs(stuck.scalar() or 0)
        except Exception:
            logger.debug("Failed to update loop gauges", exc_info=True)

    async def _update_budget_gauges(self, db) -> None:
        """Refresh per-user budget-remaining gauges for /metrics.

        The gauge is labelled by ``user_id``; budget is tracked per workspace,
        so resolve each configured user's workspace and emit its remaining
        daily budget. Best-effort — never break the health tick on error."""
        budget = getattr(getattr(self, "_orchestrator", None), "_budget", None)
        user_ids = getattr(self, "_user_ids", None) or []
        if budget is None or not user_ids:
            return
        try:
            from src.services.metrics_service import MetricsService
            from src.services.workspace_resolver import resolve_workspace_id

            for user_id in user_ids:
                try:
                    workspace_id = await resolve_workspace_id(db, user_id)
                    status = await budget.get_budget_status(db, workspace_id=workspace_id)
                    MetricsService.set_budget_remaining(user_id, status.remaining_usd)
                except Exception:
                    logger.debug("Budget gauge update failed for user %s", user_id, exc_info=True)
        except Exception:
            logger.debug("Failed to update budget gauges", exc_info=True)

    async def _reap_idle_mcp_sessions(self) -> None:
        # Reap idle MCP sessions as a safety net (sessions not closed by TurnScope).
        try:
            from src.connectors.mcp_bridge import get_session_pool

            pool = get_session_pool()
            if pool is not None:
                await pool.cleanup_idle()
        except Exception:
            logger.warning("Idle MCP session reaper failed", exc_info=True)
