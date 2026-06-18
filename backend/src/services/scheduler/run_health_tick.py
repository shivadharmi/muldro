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

                if remediated or expired_cancelled:
                    logger.info(
                        "Health check: %d stuck runs timed out, %d expired-approval runs cancelled",
                        remediated,
                        expired_cancelled,
                    )
        except Exception:
            logger.warning("Run health check failed", exc_info=True)

        # Reap idle MCP sessions as a safety net (sessions not closed by TurnScope).
        try:
            from src.connectors.mcp_bridge import get_session_pool

            pool = get_session_pool()
            if pool is not None:
                await pool.cleanup_idle()
        except Exception:
            logger.warning("Idle MCP session reaper failed", exc_info=True)
