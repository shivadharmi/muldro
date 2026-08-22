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
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings

if TYPE_CHECKING:
    from src.services.vector_store import VectorStore
from src.deep_runtime.middleware.approval_persistence import PREPARED_KEY
from src.models.approvals import Approval
from src.models.memory import Memory
from src.models.perception_state import PerceptionState
from src.models.plans import Plan
from src.models.schedules import Schedule
from src.models.task_graph import TaskRun
from src.services.audit import AuditService
from src.services.execution_state import InvalidTransitionError, transition_run
from src.services.prepared_expiry_notice import expired_prepared_notice

logger = logging.getLogger(__name__)


class HeartbeatService:
    """Periodic system maintenance and re-evaluation."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        vector_store: "VectorStore | None" = None,
        *,
        notifier=None,
    ):
        self._settings = settings
        self._db = db
        self._vector_store = vector_store
        # Optional. Expiry is recorded in the audit trail regardless; the notifier only
        # decides whether the founder is *told* in the moment, and an unreachable one
        # must never hold up the expiry itself.
        self._notifier = notifier

    STALE_THRESHOLDS = {
        "gmail": "observation_stale_gmail_minutes",
        "calendar": "observation_stale_calendar_minutes",
        "github": "observation_stale_github_minutes",
    }
    DEFAULT_STALE_MINUTES = 60

    async def run(self, user_id: str) -> dict:
        """Execute a full heartbeat cycle. Returns summary of actions taken."""
        expired_count = await self._expire_stale_memories(user_id)
        stale_plans = await self._find_stale_plans(user_id)
        escalated = await self._escalate_overdue_plans(user_id, stale_plans)
        expired_approvals = await self._expire_approvals(user_id)
        invalidated_plans = await self._invalidate_old_plans(user_id)
        observation_health = await self._check_observation_health(user_id)
        schedule_proposals = await self._reflect_on_schedules(user_id)

        summary = {
            "expired_memories": expired_count,
            "stale_plans_found": len(stale_plans),
            "plans_escalated": escalated,
            "expired_approvals": expired_approvals,
            "invalidated_plans": invalidated_plans,
            "observation_health": observation_health,
            "schedule_proposals": schedule_proposals,
            "dlq_retried": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        stale_sources = [s["source"] for s in observation_health if s["is_stale"]]
        logger.info(
            "Heartbeat completed for %s: expired_mem=%d stale=%d escalated=%d "
            "expired_apr=%d invalidated=%d stale_obs=%s sched_proposals=%d",
            user_id,
            expired_count,
            len(stale_plans),
            escalated,
            expired_approvals,
            invalidated_plans,
            stale_sources,
            len(schedule_proposals),
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
        expired_ids: list[str] = []
        for mem in memories:
            if mem.created_at and mem.ttl_days:
                expiry = mem.created_at + timedelta(days=mem.ttl_days)
                if now > expiry:
                    mem.status = "expired"
                    expired_count += 1
                    expired_ids.append(mem.memory_id)

        if expired_count:
            await self._db.flush()
            logger.info("Expired %d memories for %s", expired_count, user_id)

        # Cascade: delete expired memory vectors from Qdrant
        if expired_ids and self._vector_store:
            for mid in expired_ids:
                try:
                    await self._vector_store.delete("memories", mid)
                except Exception:
                    logger.debug("Qdrant delete failed for memory %s", mid, exc_info=True)

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
        """Expire pending approvals past their deadline.

        Expiry is the third outcome of an approval and the only one nobody chose, so it
        is recorded at least as carefully as approve and reject: an audit row per
        approval, and — for staged work, which has no run to surface its own demise —
        one batched notification per cycle.
        """
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

        audit = AuditService(self._db, self._settings)
        expired_prepared: list[Approval] = []

        for approval in approvals:
            approval.status = "expired"
            approval.decided_at = now

            # Same discriminator the approve and reject routes use, so the three sites
            # that must agree on "is this staged work?" cannot drift apart.
            is_prepared = (approval.artifact_refs or {}).get(PREPARED_KEY) is True
            if is_prepared:
                expired_prepared.append(approval)

            # Deliberately not wrapped in a swallowing except: an unwritable audit row
            # means a staged external write vanished with no record at all, which is the
            # failure this whole path exists to prevent.
            await audit.log(
                user_id=approval.user_id or user_id,
                action_type="approval_expired",
                workspace_id=approval.workspace_id or "",
                approval_id=approval.approval_id,
                execution_id=approval.execution_id or None,
                summary=f"Expired unreviewed: {approval.title}",
                policy_decision="expired_unanswered",
                details={
                    "approval_type": approval.approval_type,
                    "risk_level": approval.risk_level,
                    "expires_at": (
                        approval.expires_at.isoformat() if approval.expires_at else None
                    ),
                    "prepared": is_prepared,
                },
            )

            # A prepared action has no run and no step by design — it is a single tool
            # call recorded mid-turn, not an agentic unit. Looking one up would query for
            # the empty string and always miss.
            if not approval.execution_id:
                continue

            # Cancel the associated execution. Goes through ``transition_run`` —
            # never a direct status assignment — so the transition is VALIDATED and
            # the run records both when it ended and why. A bare ``cancelled`` with
            # no ``completed_at`` and no ``error`` is indistinguishable from a
            # user-cancelled run, which is precisely how a stalled autonomous run
            # reads to its owner as an unexplained failure.
            exec_result = await self._db.execute(
                select(TaskRun).where(TaskRun.run_id == approval.execution_id)
            )
            run = exec_result.scalar_one_or_none()
            if run and run.status == "awaiting_approval":
                try:
                    transition_run(run, "cancelled")
                except InvalidTransitionError:
                    logger.warning(
                        "Could not cancel run %s on approval expiry (status=%s)",
                        run.run_id,
                        run.status,
                    )
                    continue
                run.completed_at = now
                run.error = {
                    "type": "approval_expired",
                    "message": (
                        "Cancelled — the approval this run was waiting on expired "
                        f"unanswered at {approval.expires_at.isoformat()}"
                        if approval.expires_at
                        else "Cancelled — the approval this run was waiting on expired unanswered"
                    ),
                    "approval_id": approval.approval_id,
                }

        if approvals:
            await self._db.flush()
            logger.info("Expired %d approvals for %s", len(approvals), user_id)

        await self._notify_expired_prepared(user_id, expired_prepared)

        return len(approvals)

    async def _notify_expired_prepared(self, user_id: str, prepared: list[Approval]) -> None:
        """Tell the founder that staged work was dropped — once per cycle, never per item.

        Run-linked approvals are left out: their run is cancelled with an explanatory
        error and shows up in the feed on its own. Staged work has no run, which is
        exactly why it needs saying.
        """
        if not prepared:
            return
        if self._notifier is None:
            logger.debug(
                "%d prepared actions expired for %s with no notifier — audit only",
                len(prepared),
                user_id,
            )
            return

        # One message per workspace: a single notification listing another workspace's
        # action titles would carry them across the isolation boundary.
        by_workspace: dict[str, list[Approval]] = {}
        for approval in prepared:
            by_workspace.setdefault(approval.workspace_id or "", []).append(approval)

        for workspace_id, batch in by_workspace.items():
            notice = expired_prepared_notice(batch)
            if notice is None:
                continue
            title, body = notice
            try:
                await self._notifier.notify(
                    user_id=user_id,
                    notification_type="info_update",
                    title=title,
                    body=body,
                    data={
                        "approval_ids": [a.approval_id for a in batch],
                        # Dropped irreversible work outranks a routine update. Delivery
                        # does not hang on clearing the priority threshold, though — the
                        # persisted row plus the pending-notification tick is what
                        # guarantees it eventually lands.
                        "urgency": 0.9,
                    },
                    workspace_id=workspace_id,
                )
            except Exception:
                # Never at the cost of the expiry itself: the approvals are already
                # marked and audited, and rolling that back to retry a message would
                # trade a durable record for a transient one.
                logger.warning(
                    "Could not notify %s that %d prepared actions expired",
                    user_id,
                    len(batch),
                    exc_info=True,
                )

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

    async def _check_observation_health(self, user_id: str) -> list[dict]:
        """Check observation freshness and flag stale sources."""
        now = datetime.now(timezone.utc)

        result = await self._db.execute(
            select(PerceptionState).where(PerceptionState.user_id == user_id)
        )
        states = list(result.scalars().all())

        health = []
        for ps in states:
            attr_name = self.STALE_THRESHOLDS.get(ps.source)
            stale_minutes = (
                getattr(self._settings, attr_name, self.DEFAULT_STALE_MINUTES)
                if attr_name
                else self.DEFAULT_STALE_MINUTES
            )
            threshold = now - timedelta(minutes=stale_minutes)
            last_run = ps.last_run_at
            is_stale = ps.circuit_state == "open" or last_run is None or last_run < threshold

            status = "error" if ps.circuit_state == "open" else "ok"
            if is_stale and ps.circuit_state != "open":
                status = "stale"

            health.append(
                {
                    "source": ps.source,
                    "last_observed_at": last_run.isoformat() if last_run else None,
                    "status": status,
                    "is_stale": is_stale,
                }
            )

        if any(h["is_stale"] for h in health):
            stale = [h["source"] for h in health if h["is_stale"]]
            logger.warning("Stale observation sources for %s: %s", user_id, stale)

        return health

    async def _reflect_on_schedules(self, user_id: str) -> list[dict]:
        """Reflect on schedule health and propose adjustments.

        Rules:
        - Observation schedule with 0 items found for 10+ runs → propose reducing frequency
        - 3+ consecutive failures → flag for user attention
        """
        proposals: list[dict] = []

        # Get observation-type schedules
        result = await self._db.execute(
            select(Schedule).where(
                Schedule.user_id == user_id,
                Schedule.action_type == "observe_source",
                Schedule.enabled.is_(True),
            )
        )
        obs_schedules = list(result.scalars().all())

        # Cross-reference with perception state
        ps_result = await self._db.execute(
            select(PerceptionState).where(PerceptionState.user_id == user_id)
        )
        ps_by_source = {ps.source: ps for ps in ps_result.scalars().all()}

        for sched in obs_schedules:
            source = (sched.action_config or {}).get("source", "")
            ps = ps_by_source.get(source)

            # Check for consistently empty observations (heuristic: high run_count but
            # perception shows 0 items)
            if ps and ps.last_event_count == 0 and sched.run_count >= 10:
                proposals.append(
                    {
                        "schedule_id": sched.schedule_id,
                        "name": sched.name,
                        "proposal": "reduce_frequency",
                        "reason": (
                            f"Source '{source}' has returned 0 items for the last observation "
                            f"after {sched.run_count} runs. Consider reducing check frequency."
                        ),
                    }
                )

        # Check all schedules for consecutive failures
        all_result = await self._db.execute(
            select(Schedule).where(
                Schedule.user_id == user_id,
                Schedule.consecutive_failures >= 3,
                Schedule.enabled.is_(True),
            )
        )
        failing_schedules = list(all_result.scalars().all())

        for sched in failing_schedules:
            proposals.append(
                {
                    "schedule_id": sched.schedule_id,
                    "name": sched.name,
                    "proposal": "investigate_failures",
                    "reason": (
                        f"Schedule '{sched.name}' has {sched.consecutive_failures} consecutive "
                        f"failures. Last error: {sched.last_error or 'unknown'}"
                    ),
                }
            )

        if proposals:
            logger.info("Schedule reflection for %s: %d proposals", user_id, len(proposals))

        return proposals
