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
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from ulid import ULID

from src.contracts import PolicyDecision
from src.models.plans import Plan
from src.models.task_graph import TaskRun
from src.services.audit import AuditService

if TYPE_CHECKING:
    from src.services.settings_service import SettingsService
    from src.services.trust_engine import TrustEngine

logger = logging.getLogger(__name__)

VALID_POLICY_MODES = {"lockdown", "approval_required", "suggest_only", "full_auto"}

_DECISION_TO_RUN_STATUS = {
    "auto_execute": "pending",
    "auto_execute_notify": "pending",
    "auto_execute_silent": "pending",
    "approval_required": "awaiting_approval",
    "blocked": "cancelled",
}


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

        policy_decision = await self._apply_policy(plan, user_id, workspace_id)

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
            status=_DECISION_TO_RUN_STATUS.get(policy_decision, "pending"),
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

        first_task_cap = "plan_execution"
        if plan.tasks:
            first_task_cap = plan.tasks[0].task_type or (plan.tasks[0].input_data or {}).get(
                "capability", "plan_execution"
            )

        approval = await create_approval(
            self._db,
            user_id=user_id,
            workspace_id=workspace_id,
            approval_type=first_task_cap,
            title=f"Approve: {plan.goal}",
            summary=plan.reasoning_summary,
            risk_level=plan.risk_level or "medium",
            execution_id=execution_id,
            requested_by=user_id,
            artifact_refs={"plan_id": plan.plan_id},
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

    async def _get_policy_mode(self, user_id: str) -> str:
        """Get policy mode from user settings, with fallback."""
        if self._settings_service:
            try:
                mode = await self._settings_service.get_policy_mode(user_id)
                if mode in VALID_POLICY_MODES:
                    return mode
            except Exception:
                logger.warning("Failed to read policy mode for %s", user_id, exc_info=True)
        return "approval_required"

    async def _check_trust(self, workspace_id: str, capability: str, risk_level: str) -> bool:
        """Check if TrustEngine recommends auto-execution for a plan."""
        if not self._trust_engine:
            return False
        try:
            decision = await self._trust_engine.evaluate_plan_risk(
                capability=capability,
                risk_level=risk_level,
                workspace_id=workspace_id,
            )
            return decision.decision in (
                "auto_execute",
                "auto_execute_notify",
                "auto_execute_silent",
            )
        except Exception:
            logger.warning("Trust engine check failed", exc_info=True)
            return False

    async def _apply_policy(self, plan: Plan, user_id: str, workspace_id: str = "") -> str:
        """Apply policy rules based on plan risk level and user settings."""
        risk = plan.risk_level or "low"
        policy_mode = await self._get_policy_mode(user_id)

        # Lockdown: block everything
        if policy_mode == "lockdown":
            return "blocked"

        # Suggest-only: never execute
        if policy_mode == "suggest_only":
            return "blocked"

        # Critical risk always requires approval, even in full_auto
        if risk == "critical":
            return "approval_required"

        # Full auto mode: auto-execute unless high-risk
        if policy_mode == "full_auto":
            if risk == "high":
                return "approval_required"
            return "auto_execute"

        # Default: approval_required mode
        if risk == "high":
            return "approval_required"

        # Extract capability from first task for trust checks
        first_cap = "plan_execution"
        if plan.tasks:
            first_cap = plan.tasks[0].task_type or (plan.tasks[0].input_data or {}).get(
                "capability", "plan_execution"
            )

        # Trust-based graduation for medium-risk
        if risk == "medium":
            if await self._check_trust(workspace_id, first_cap, risk):
                return "auto_execute"
            return "approval_required"

        # Low/none risk in approval_required mode — check trust
        if await self._check_trust(workspace_id, first_cap, risk):
            return "auto_execute"

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
