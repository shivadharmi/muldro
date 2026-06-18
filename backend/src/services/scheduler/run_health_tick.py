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

        await self._reap_idle_mcp_sessions()

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
