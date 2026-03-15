"""Startup recovery for the Jarvis orchestrator.

On every boot, reconciles in-flight state:
- Orphaned plans (planned but never policy-checked)
- Stale executions (executing with no heartbeat)
- Expired approvals (past TTL)
- Restores observation cursors
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.approvals import Approval
from src.models.executions import Execution
from src.models.plans import Plan

logger = logging.getLogger(__name__)


async def run_startup_recovery(db: AsyncSession) -> dict:
    """Run on application startup before accepting requests.

    Returns a summary of recovered items.
    """
    summary = {
        "orphaned_plans": 0,
        "stale_executions": 0,
        "expired_approvals": 0,
    }

    now = datetime.now(timezone.utc)

    # 1. Orphaned plans: planned but never policy-checked (older than 1 hour)
    try:
        cutoff = now - timedelta(hours=1)
        result = await db.execute(
            select(Plan).where(
                Plan.status == "planned",
                Plan.created_at < cutoff,
            )
        )
        orphaned = result.scalars().all()
        for plan in orphaned:
            plan.status = "stale_on_recovery"
            logger.warning(
                "recovery_orphaned_plan",
                extra={"plan_id": plan.plan_id, "goal": plan.goal},
            )
        summary["orphaned_plans"] = len(orphaned)
    except Exception as e:
        logger.error("Recovery: orphaned plans scan failed: %s", e)

    # 2. Stale executions: executing with no recent update (older than 15 min)
    try:
        exec_cutoff = now - timedelta(minutes=15)
        result = await db.execute(
            select(Execution).where(
                Execution.status == "running",
                Execution.updated_at < exec_cutoff,
            )
        )
        stale = result.scalars().all()
        for execution in stale:
            execution.status = "failed"
            execution.error_message = "stale_on_recovery"
            logger.warning(
                "recovery_stale_execution",
                extra={"execution_id": execution.execution_id},
            )
        summary["stale_executions"] = len(stale)
    except Exception as e:
        logger.error("Recovery: stale executions scan failed: %s", e)

    # 3. Expired approvals: pending past TTL
    try:
        result = await db.execute(
            select(Approval).where(
                Approval.status == "pending",
                Approval.expires_at.isnot(None),
                Approval.expires_at < now,
            )
        )
        expired = result.scalars().all()
        for approval in expired:
            approval.status = "expired"
            logger.warning(
                "recovery_expired_approval",
                extra={
                    "approval_id": approval.approval_id,
                    "title": approval.title,
                },
            )
        summary["expired_approvals"] = len(expired)
    except Exception as e:
        logger.error("Recovery: expired approvals scan failed: %s", e)

    try:
        await db.commit()
    except Exception as e:
        logger.error("Recovery: commit failed: %s", e)
        await db.rollback()

    logger.info(
        "startup_recovery_complete",
        extra=summary,
    )
    return summary
