"""Heartbeat Service — periodic re-evaluation of priorities and plans.

Runs on a cron schedule (or manual trigger) to:
- Re-score stale plans based on updated context
- Expire old memories past their TTL
- Invalidate plans past their TTL
- Expire pending approvals past their deadline
- Detect events that need attention but haven't been acted on
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.models.approvals import Approval
from src.models.executions import Execution
from src.models.memory import Memory
from src.models.plans import Plan

logger = logging.getLogger(__name__)


class HeartbeatService:
    """Periodic system maintenance and re-evaluation."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db

    async def run(self, user_id: str) -> dict:
        """Execute a full heartbeat cycle. Returns summary of actions taken."""
        expired_count = await self._expire_stale_memories(user_id)
        stale_plans = await self._find_stale_plans(user_id)
        escalated = await self._escalate_overdue_plans(user_id, stale_plans)
        expired_approvals = await self._expire_approvals(user_id)
        invalidated_plans = await self._invalidate_old_plans(user_id)

        summary = {
            "expired_memories": expired_count,
            "stale_plans_found": len(stale_plans),
            "plans_escalated": escalated,
            "expired_approvals": expired_approvals,
            "invalidated_plans": invalidated_plans,
            "dlq_retried": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Heartbeat completed for %s: expired_mem=%d stale=%d escalated=%d "
            "expired_apr=%d invalidated=%d",
            user_id,
            expired_count,
            len(stale_plans),
            escalated,
            expired_approvals,
            invalidated_plans,
        )
        return summary

    async def _expire_stale_memories(self, user_id: str) -> int:
        """Expire memories past their TTL."""
        now = datetime.now(timezone.utc)

        result = await self._db.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.status == "active",
                Memory.ttl_days.isnot(None),
            )
        )
        memories = result.scalars().all()

        expired_count = 0
        for mem in memories:
            if mem.created_at and mem.ttl_days:
                expiry = mem.created_at + timedelta(days=mem.ttl_days)
                if now > expiry:
                    mem.status = "expired"
                    expired_count += 1

        if expired_count:
            await self._db.flush()
            logger.info("Expired %d memories for %s", expired_count, user_id)

        return expired_count

    async def _find_stale_plans(self, user_id: str) -> list[Plan]:
        """Find plans that have been sitting in 'created' or 'policy_checked'
        status for too long without execution."""
        stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        result = await self._db.execute(
            select(Plan).where(
                Plan.user_id == user_id,
                Plan.status.in_(["created", "policy_checked"]),
                Plan.created_at < stale_cutoff,
            )
        )
        return list(result.scalars().all())

    async def _escalate_overdue_plans(self, user_id: str, stale_plans: list[Plan]) -> int:
        """Escalate stale plans by bumping priority or marking for review."""
        escalated = 0
        priority_order = ["low", "medium", "high", "critical"]

        for plan in stale_plans:
            current_idx = priority_order.index(plan.priority or "medium")
            if current_idx < len(priority_order) - 1:
                plan.priority = priority_order[current_idx + 1]
                escalated += 1
                logger.info(
                    "Escalated plan %s: %s → %s",
                    plan.plan_id,
                    priority_order[current_idx],
                    plan.priority,
                )

        if escalated:
            await self._db.flush()

        return escalated

    async def _expire_approvals(self, user_id: str) -> int:
        """Expire pending approvals past their deadline."""
        now = datetime.now(timezone.utc)

        result = await self._db.execute(
            select(Approval).where(
                Approval.user_id == user_id,
                Approval.status == "pending",
                Approval.expires_at.isnot(None),
                Approval.expires_at < now,
            )
        )
        approvals = list(result.scalars().all())

        for approval in approvals:
            approval.status = "expired"
            approval.decided_at = now

            # Cancel the associated execution
            exec_result = await self._db.execute(
                select(Execution).where(Execution.execution_id == approval.execution_id)
            )
            execution = exec_result.scalar_one_or_none()
            if execution and execution.status == "awaiting_approval":
                execution.status = "cancelled"

        if approvals:
            await self._db.flush()
            logger.info("Expired %d approvals for %s", len(approvals), user_id)

        return len(approvals)

    async def _invalidate_old_plans(self, user_id: str) -> int:
        """Invalidate plans older than the configured TTL that are still unexecuted."""
        ttl_hours = getattr(self._settings, "plan_ttl_hours", 72)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)

        result = await self._db.execute(
            select(Plan).where(
                Plan.user_id == user_id,
                Plan.status.in_(["created", "policy_checked"]),
                Plan.created_at < cutoff,
            )
        )
        plans = list(result.scalars().all())

        for plan in plans:
            plan.status = "failed"
            logger.info(
                "Invalidated stale plan %s (created %s, TTL %dh)",
                plan.plan_id,
                plan.created_at,
                ttl_hours,
            )

        if plans:
            await self._db.flush()

        return len(plans)
