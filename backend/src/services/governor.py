"""Governor — enforces trust and safety policies.

Sits before every meaningful execution. Evaluates whether an action
is allowed, needs approval, or should be blocked.

Responsibilities:
- Evaluate action policies based on plan decision and risk level
- Create TaskRun records from plans
- Create Approval records when approval_required
- Log all policy decisions to audit trail
- Integrate with TrustEngine for graduated autonomy
- Read per-user policy mode from SettingsService

Policy Modes:
- lockdown: All actions blocked
- approval_required: All actions need approval (default)
- suggest_only: Jarvis suggests, never acts
- full_auto: Jarvis acts autonomously (still respects trust scores)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from ulid import ULID

from src.models.plans import Plan
from src.models.task_graph import TaskRun
from src.orchestrator.contracts import PolicyDecision
from src.services.audit import AuditService

if TYPE_CHECKING:
    from src.services.settings_service import SettingsService
    from src.services.trust_engine import TrustEngine

logger = logging.getLogger(__name__)

# Default action classification (used when no per-user settings override)
APPROVAL_REQUIRED_ACTIONS = {
    "draft_reply",
    "draft_email",
    "send_email",
    "create_event",
    "update_task",
    "post_message",
}

AUTO_EXECUTE_DECISIONS = {
    "fetch_info",
    "summarize",
    "search",
    "add_to_brief",
    "acknowledge",
    "answer_directly",
}

CRITICAL_ACTIONS = {
    "payment",
    "deploy",
    "delete_data",
    "modify_permissions",
    "security_change",
}

BLOCKED_ACTIONS = {
    "delete_data",
    "modify_permissions",
}

VALID_POLICY_MODES = {"lockdown", "approval_required", "suggest_only", "full_auto"}


class Governor:
    """Evaluate plans against safety policies with graduated trust."""

    def __init__(
        self,
        db: AsyncSession,
        notifier=None,
        trust_engine: TrustEngine | None = None,
        settings_service: SettingsService | None = None,
        event_bus=None,
    ):
        self._db = db
        self._audit = AuditService(db)
        self._notifier = notifier
        self._trust_engine = trust_engine
        self._settings_service = settings_service
        self._event_bus = event_bus

    async def evaluate_plan(
        self, plan_id: str, user_id: str, workspace_id: str = ""
    ) -> PolicyDecision:
        """Evaluate a plan and determine execution mode.

        Creates a TaskRun record. If approval is needed, also creates
        an Approval record.

        Returns: PolicyDecision with decision, run_id, and justification.
        """
        result = await self._db.execute(
            select(Plan).options(selectinload(Plan.tasks)).where(Plan.plan_id == plan_id)
        )
        plan = result.scalar_one_or_none()
        if not plan:
            logger.warning("Plan not found for governance: %s", plan_id)
            return PolicyDecision(
                decision="blocked", justification="Plan not found", risk_level="high"
            )

        policy_decision = await self._apply_policy(plan, user_id)

        run_id = f"run_{ULID()}"
        run = TaskRun(
            run_id=run_id,
            plan_id=plan_id,
            user_id=user_id,
            workspace_id=workspace_id,
            source="plan",
            execution_mode=policy_decision,
            policy_decision={
                "decision": policy_decision,
                "risk_level": plan.risk_level or "low",
            },
            status="pending" if policy_decision == "auto_execute" else policy_decision,
        )
        self._db.add(run)

        plan.status = "policy_checked"
        plan.execution_mode = policy_decision

        await self._audit.log(
            user_id=user_id,
            action_type="policy_evaluated",
            plan_id=plan_id,
            execution_id=run_id,
            policy_decision=policy_decision,
            summary=f"Plan '{plan.goal}' → {policy_decision}",
            workspace_id=workspace_id,
        )

        approval_id = None
        if policy_decision == "approval_required":
            approval_id = await self._create_approval(plan, run_id, user_id, workspace_id)
            run.status = "awaiting_approval"
            logger.info(
                "Approval created: %s for plan %s",
                approval_id,
                plan_id,
            )

            if self._notifier:
                try:
                    await self._notifier.notify(
                        user_id=user_id,
                        notification_type="approval_request",
                        title=f"Approval needed: {plan.goal}",
                        body="Plan requires approval before execution.",
                        data={
                            "approval_id": approval_id,
                            "plan_id": plan_id,
                            "risk_level": plan.risk_level or "medium",
                        },
                        workspace_id=workspace_id,
                    )
                except Exception:
                    logger.warning("Failed to notify for approval", exc_info=True)

        if policy_decision == "blocked":
            run.status = "cancelled"
            plan.status = "blocked"

        await self._db.commit()

        # Emit domain events
        event_type = (
            "plan.approved"
            if policy_decision == "auto_execute"
            else "plan.rejected"
            if policy_decision == "blocked"
            else "approval.requested"
        )
        await self._emit_event(
            event_type,
            user_id,
            {
                "plan_id": plan_id,
                "run_id": run_id,
                "policy_decision": policy_decision,
            },
        )

        logger.info(
            "Governor: plan=%s decision=%s run=%s",
            plan_id,
            policy_decision,
            run_id,
        )
        return PolicyDecision(
            decision=policy_decision,
            justification=f"Plan '{plan.goal}' evaluated as {policy_decision}",
            risk_level=plan.risk_level or "low",
            approval_id=approval_id,
            execution_id=run_id,
        )

    async def _create_approval(
        self, plan: Plan, execution_id: str, user_id: str, workspace_id: str = ""
    ) -> str:
        """Create an approval record for a plan requiring user consent."""
        from src.services.approval_service import create_approval

        task_types = []
        if plan.tasks:
            task_types = [t.task_type for t in plan.tasks]

        approval = await create_approval(
            self._db,
            user_id=user_id,
            workspace_id=workspace_id,
            approval_type=task_types[0] if task_types else plan.decision,
            title=f"Approve: {plan.goal}",
            summary=plan.reasoning_summary,
            risk_level=plan.risk_level or "medium",
            execution_id=execution_id,
            requested_by=user_id,
            artifact_refs={"plan_id": plan.plan_id, "task_types": task_types},
        )

        await self._audit.log(
            user_id=user_id,
            action_type="approval_requested",
            plan_id=plan.plan_id,
            execution_id=execution_id,
            approval_id=approval.approval_id,
            summary=f"Approval requested: {plan.goal}",
            workspace_id=workspace_id,
        )

        return approval.approval_id

    async def _get_time_based_policy_override(self, user_id: str) -> str | None:
        """Check if a time-based policy override applies for the current time.

        Time policies are stored in user settings as:
        {
            "time_policies": [
                {"start_hour": 9, "end_hour": 17, "mode": "full_auto", "days": [0,1,2,3,4]},
                {"start_hour": 22, "end_hour": 6, "mode": "lockdown"}
            ]
        }
        Returns the policy mode if a time-based rule matches, or None.
        """
        if not self._settings_service:
            return None

        try:
            time_policies = await self._settings_service.get(user_id, "policy", "time_policies")
            if not time_policies or not isinstance(time_policies, list):
                return None

            now = datetime.now(timezone.utc)
            current_hour = now.hour
            current_day = now.weekday()  # 0=Monday, 6=Sunday

            for policy in time_policies:
                if not isinstance(policy, dict):
                    continue

                start_hour = policy.get("start_hour")
                end_hour = policy.get("end_hour")
                mode = policy.get("mode")
                days = policy.get("days")  # Optional day-of-week filter

                if start_hour is None or end_hour is None or not mode:
                    continue

                # Check day-of-week filter if present
                if days is not None:
                    if not isinstance(days, list) or current_day not in days:
                        continue

                # Check if current hour falls within the time range
                if start_hour <= end_hour:
                    # Normal range (e.g., 9:00 to 17:00)
                    in_range = start_hour <= current_hour < end_hour
                else:
                    # Overnight range (e.g., 22:00 to 06:00)
                    in_range = current_hour >= start_hour or current_hour < end_hour

                if in_range and mode in VALID_POLICY_MODES:
                    logger.info(
                        "Time-based policy override: user=%s mode=%s (hour=%d)",
                        user_id,
                        mode,
                        current_hour,
                    )
                    return mode

        except Exception:
            logger.warning("Failed to read time-based policies for %s", user_id, exc_info=True)

        return None

    async def _get_policy_mode(self, user_id: str) -> str:
        """Get policy mode from user settings, with fallback."""
        # First check time-based override
        time_override = await self._get_time_based_policy_override(user_id)
        if time_override:
            return time_override

        # Then fall back to user's default policy mode
        if self._settings_service:
            try:
                mode = await self._settings_service.get_policy_mode(user_id)
                if mode in VALID_POLICY_MODES:
                    return mode
            except Exception:
                logger.warning("Failed to read policy mode for %s", user_id, exc_info=True)
        return "approval_required"

    async def _check_trust(self, user_id: str, action_type: str, risk_level: str) -> bool:
        """Check if the trust engine recommends auto-approval."""
        if not self._trust_engine:
            return False
        try:
            return await self._trust_engine.should_auto_approve(user_id, action_type, risk_level)
        except Exception:
            logger.warning("Trust engine check failed", exc_info=True)
            return False

    async def _apply_policy(self, plan: Plan, user_id: str) -> str:
        """Apply policy rules considering user settings and trust scores."""
        decision = plan.decision or ""
        risk = plan.risk_level or "low"
        policy_mode = await self._get_policy_mode(user_id)

        # Lockdown: block everything
        if policy_mode == "lockdown":
            return "blocked"

        # Suggest-only: never execute, always just suggest
        if policy_mode == "suggest_only":
            return "blocked"

        # Always block dangerous actions regardless of mode
        if decision in BLOCKED_ACTIONS:
            return "blocked"

        # Critical actions always require approval, even in full_auto
        if decision in CRITICAL_ACTIONS or risk == "critical":
            return "approval_required"

        # Full auto mode: auto-execute unless high-risk or blocked
        if policy_mode == "full_auto":
            if risk == "high":
                return "approval_required"
            return "auto_execute"

        # Default: approval_required mode with trust-based graduation
        if risk == "high":
            return "approval_required"

        if decision in APPROVAL_REQUIRED_ACTIONS:
            # Check trust engine for graduated autonomy
            if await self._check_trust(user_id, decision, risk):
                logger.info("Trust-based auto-approve: user=%s action=%s", user_id, decision)
                return "auto_execute"
            return "approval_required"

        if decision in AUTO_EXECUTE_DECISIONS:
            return "auto_execute"

        # Check task types for external actions
        if plan.tasks:
            for task in plan.tasks:
                if task.task_type in APPROVAL_REQUIRED_ACTIONS:
                    if await self._check_trust(user_id, task.task_type, risk):
                        continue
                    return "approval_required"

        # Default: require approval for safety
        return "approval_required"

    async def is_auto_execute_tool(self, tool_name: str) -> bool:
        """Check if a tool can auto-execute based on registry risk metadata.

        Tool-level policy: derives from risk_level + requires_approval.
        Decision-level policy (AUTO_EXECUTE_DECISIONS) is separate and unchanged.
        """
        from src.services.tool_registry import ToolRegistry

        registry = ToolRegistry(self._db)
        tool = await registry.get_tool(tool_name)
        if not tool:
            return False
        return tool.risk_level == "low" and not tool.requires_approval

    async def _emit_event(self, event_type: str, user_id: str, payload: dict) -> None:
        """Publish a domain event (best-effort)."""
        if not self._event_bus:
            return
        try:
            stream = self._event_bus.agent_stream(user_id)
            await self._event_bus.publish(stream, event_type, payload, user_id)
        except Exception:
            logger.debug("Failed to emit %s event", event_type, exc_info=True)
