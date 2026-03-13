"""Governor — enforces trust and safety policies.

Sits before every meaningful execution. Evaluates whether an action
is allowed, needs approval, or should be blocked.

Responsibilities:
- Evaluate action policies based on plan decision and risk level
- Create Execution records from plans
- Create Approval records when approval_required
- Log all policy decisions to audit trail

Policy Rules v0:
- All external writes (send_email, create_event) → approval_required
- Read-only operations → auto_execute
- Unknown/high-risk actions → blocked
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.approvals import Approval
from src.models.executions import Execution
from src.models.plans import Plan
from src.services.audit import AuditService

logger = logging.getLogger(__name__)

# v0 policy: action types that require approval
APPROVAL_REQUIRED_ACTIONS = {
    "draft_reply",
    "draft_email",
    "send_email",
    "create_event",
    "update_task",
    "post_message",
}

# v0 policy: action types that are auto-executable
AUTO_EXECUTE_ACTIONS = {
    "fetch_info",
    "summarize",
    "search_memory",
    "add_to_brief",
    "acknowledge",
    "answer_directly",
}

BLOCKED_ACTIONS = {
    "delete_data",
    "modify_permissions",
}


class Governor:
    """Evaluate plans against safety policies."""

    def __init__(self, db: AsyncSession):
        self._db = db
        self._audit = AuditService(db)

    async def evaluate_plan(self, plan_id: str, user_id: str) -> str:
        """Evaluate a plan and determine execution mode.

        Creates an Execution record. If approval is needed, also creates
        an Approval record.

        Returns: 'auto_execute', 'approval_required', or 'blocked'
        """
        result = await self._db.execute(select(Plan).where(Plan.plan_id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            logger.warning("Plan not found for governance: %s", plan_id)
            return "blocked"

        policy_decision = self._apply_policy(plan)

        execution_id = f"exec_{ULID()}"
        execution = Execution(
            execution_id=execution_id,
            plan_id=plan_id,
            user_id=user_id,
            status="pending" if policy_decision == "auto_execute" else policy_decision,
        )
        self._db.add(execution)

        plan.status = "policy_checked"
        plan.execution_mode = policy_decision

        await self._audit.log(
            user_id=user_id,
            action_type="policy_evaluated",
            plan_id=plan_id,
            execution_id=execution_id,
            policy_decision=policy_decision,
            summary=f"Plan '{plan.goal}' → {policy_decision}",
        )

        if policy_decision == "approval_required":
            approval_id = await self._create_approval(plan, execution_id, user_id)
            execution.status = "awaiting_approval"
            logger.info(
                "Approval created: %s for plan %s",
                approval_id,
                plan_id,
            )

        if policy_decision == "blocked":
            execution.status = "cancelled"
            plan.status = "blocked"

        await self._db.commit()

        logger.info(
            "Governor: plan=%s decision=%s exec=%s",
            plan_id,
            policy_decision,
            execution_id,
        )
        return policy_decision

    async def _create_approval(self, plan: Plan, execution_id: str, user_id: str) -> str:
        """Create an approval record for a plan requiring user consent."""
        approval_id = f"apr_{ULID()}"

        task_types = []
        if plan.tasks:
            task_types = [t.task_type for t in plan.tasks]

        approval = Approval(
            approval_id=approval_id,
            user_id=user_id,
            execution_id=execution_id,
            approval_type=task_types[0] if task_types else plan.decision,
            title=f"Approve: {plan.goal}",
            summary=plan.reasoning_summary,
            artifact_refs={"plan_id": plan.plan_id, "task_types": task_types},
            risk_level=plan.risk_level or "medium",
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        self._db.add(approval)

        await self._audit.log(
            user_id=user_id,
            action_type="approval_requested",
            plan_id=plan.plan_id,
            execution_id=execution_id,
            approval_id=approval_id,
            summary=f"Approval requested: {plan.goal}",
        )

        return approval_id

    def _apply_policy(self, plan: Plan) -> str:
        """Apply v0 policy rules to determine execution mode."""
        decision = plan.decision or ""
        risk = plan.risk_level or "low"

        if decision in BLOCKED_ACTIONS:
            return "blocked"

        if risk == "high":
            return "approval_required"

        if decision in APPROVAL_REQUIRED_ACTIONS:
            return "approval_required"

        if decision in AUTO_EXECUTE_ACTIONS:
            return "auto_execute"

        # Check task types for external actions
        if plan.tasks:
            for task in plan.tasks:
                if task.task_type in APPROVAL_REQUIRED_ACTIONS:
                    return "approval_required"

        # Default: require approval for safety
        return "approval_required"
