"""Approval Impact Service — risk analysis and evidence for approval decisions.

Provides impact summaries, reversibility assessments, policy explanations,
evidence bundles, and affected entity lists for pending approvals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.approvals import Approval
from src.models.entities import Entity
from src.models.plans import Plan
from src.models.task_graph import TaskRun, TaskStep

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImpactSummary:
    """Summary of the impact of an approval decision."""

    affected_entity_count: int
    risk_level: str
    reversibility: str  # reversible, partially_reversible, irreversible
    reversibility_detail: str
    policy_explanation: str
    downstream_effects: list[str]


@dataclass(frozen=True, slots=True)
class AffectedEntity:
    entity_id: str
    name: str
    entity_type: str
    impact_type: str  # direct, indirect


class ApprovalImpactService:
    """Analyzes the impact of approving or rejecting an action."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def get_impact(self, approval_id: str) -> ImpactSummary:
        """Build an impact summary for an approval."""
        result = await self._db.execute(
            select(Approval).where(
                Approval.approval_id == approval_id,
                Approval.workspace_id == self._workspace_id,
            )
        )
        approval = result.scalar_one_or_none()
        if not approval:
            return ImpactSummary(
                affected_entity_count=0,
                risk_level="unknown",
                reversibility="unknown",
                reversibility_detail="Approval not found",
                policy_explanation="",
                downstream_effects=[],
            )

        affected = await self.get_affected_entities(approval_id)
        reversibility, detail = _assess_reversibility(approval.approval_type)
        policy_explanation = _explain_policy(approval.approval_type, approval.risk_level)
        downstream = await self._get_downstream_effects(approval)

        return ImpactSummary(
            affected_entity_count=len(affected),
            risk_level=approval.risk_level or "medium",
            reversibility=reversibility,
            reversibility_detail=detail,
            policy_explanation=policy_explanation,
            downstream_effects=downstream,
        )

    async def get_affected_entities(self, approval_id: str) -> list[AffectedEntity]:
        """Get entities affected by this approval's action."""
        result = await self._db.execute(
            select(Approval).where(
                Approval.approval_id == approval_id,
                Approval.workspace_id == self._workspace_id,
            )
        )
        approval = result.scalar_one_or_none()
        if not approval:
            return []

        affected: list[AffectedEntity] = []

        # Get entities from the plan's context
        if approval.execution_id:
            run_result = await self._db.execute(
                select(TaskRun).where(TaskRun.run_id == approval.execution_id)
            )
            run = run_result.scalar_one_or_none()
            if run and run.plan_id:
                plan_result = await self._db.execute(
                    select(Plan).where(Plan.plan_id == run.plan_id)
                )
                plan = plan_result.scalar_one_or_none()
                if plan and plan.goal:
                    entities = await self._find_entities_in_text(plan.goal)
                    affected.extend(entities)

        # Get entities from the approval title/summary
        if approval.title:
            title_entities = await self._find_entities_in_text(approval.title)
            seen = {e.entity_id for e in affected}
            for e in title_entities:
                if e.entity_id not in seen:
                    affected.append(e)

        return affected

    async def get_step_count(self, approval_id: str) -> int:
        """Get how many steps are pending in the associated run."""
        result = await self._db.execute(select(Approval).where(Approval.approval_id == approval_id))
        approval = result.scalar_one_or_none()
        if not approval or not approval.execution_id:
            return 0

        count = await self._db.scalar(
            select(func.count())
            .select_from(TaskStep)
            .where(
                TaskStep.run_id == approval.execution_id,
                TaskStep.status == "pending",
            )
        )
        return count or 0

    async def _find_entities_in_text(self, text: str) -> list[AffectedEntity]:
        """Find entities whose names appear in the given text."""
        result = await self._db.execute(
            select(Entity)
            .where(Entity.workspace_id == self._workspace_id)
            .order_by(Entity.updated_at.desc())
            .limit(50)
        )
        text_lower = text.lower()
        matches = []
        for e in result.scalars().all():
            name = e.canonical_name or ""
            if name and name.lower() in text_lower:
                matches.append(
                    AffectedEntity(
                        entity_id=e.entity_id,
                        name=name,
                        entity_type=e.entity_type or "",
                        impact_type="direct",
                    )
                )
        return matches[:10]

    async def _get_downstream_effects(self, approval: Approval) -> list[str]:
        """Describe what will happen if this approval is granted/rejected."""
        effects: list[str] = []
        approval_type = approval.approval_type or ""

        if "email" in approval_type or "send" in approval_type:
            effects.append("An email will be sent to external recipients")
            effects.append("Recipients will see this message in their inbox")

        if "calendar" in approval_type or "event" in approval_type:
            effects.append("Calendar event will be created/modified")
            effects.append("Attendees will receive notifications")

        if "issue" in approval_type or "pr" in approval_type:
            effects.append("A GitHub/Linear issue or PR will be created/modified")

        if "slack" in approval_type or "message" in approval_type:
            effects.append("A message will be posted in a Slack channel")

        if not effects:
            effects.append("The planned action will be executed")

        if approval.execution_id:
            step_count = await self.get_step_count(approval.approval_id)
            if step_count > 0:
                effects.append(f"{step_count} pending step(s) will proceed after approval")

        return effects


def _assess_reversibility(approval_type: str | None) -> tuple[str, str]:
    """Assess whether the action can be undone."""
    if not approval_type:
        return "partially_reversible", "Unknown action type"

    irreversible_types = {"send_email", "merge_pr", "delete"}
    partial_types = {"create_issue", "post_message", "create_event"}

    if any(t in approval_type for t in irreversible_types):
        return "irreversible", "This action cannot be undone once executed"

    if any(t in approval_type for t in partial_types):
        return (
            "partially_reversible",
            "The created resource can be edited or deleted, but notifications already sent",
        )

    return "reversible", "This action can be undone or modified after execution"


def _explain_policy(approval_type: str | None, risk_level: str | None) -> str:
    """Explain why this action requires approval."""
    risk = risk_level or "medium"
    atype = approval_type or "action"

    if risk == "critical":
        return (
            f"This {atype} is classified as critical risk. "
            "All critical-risk actions require explicit approval before execution."
        )
    if risk == "high":
        return (
            f"This {atype} is classified as high risk. "
            "High-risk external writes require approval per the governor policy."
        )
    return (
        f"This {atype} requires approval because external write actions "
        "are gated by default in Muldro v1."
    )
