"""Approval endpoints — list, approve, and reject pending actions."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.api.schemas import ApprovalDecisionRequest, ApprovalDetailResponse, ApprovalResponse
from src.config.settings import Settings, get_settings
from src.models.approvals import Approval
from src.models.executions import Execution
from src.models.plans import Plan
from src.services.audit import AuditService
from src.services.operator import Operator

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/v1/approvals/{approval_id}", response_model=ApprovalDetailResponse)
async def get_approval_detail(
    approval_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Get detailed info for a single approval, including execution and plan context."""
    result = await db.execute(
        select(Approval).where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
        )
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")

    # Get plan goal via execution
    plan_goal = None
    if approval.execution_id:
        exec_result = await db.execute(
            select(Execution).where(Execution.execution_id == approval.execution_id)
        )
        execution = exec_result.scalar_one_or_none()
        if execution:
            plan_result = await db.execute(
                select(Plan.goal).where(Plan.plan_id == execution.plan_id)
            )
            plan_goal = plan_result.scalar_one_or_none()

    return ApprovalDetailResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        approval_type=approval.approval_type,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
        decision_reason=approval.decision_reason,
        execution_id=approval.execution_id,
        plan_goal=plan_goal,
        artifact_refs=approval.artifact_refs,
    )


@router.get("/v1/approvals", response_model=list[ApprovalResponse])
async def list_approvals(
    status: str = "pending",
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """List approvals for the user, filtered by status."""
    result = await db.execute(
        select(Approval)
        .where(Approval.user_id == user_id, Approval.status == status)
        .order_by(Approval.created_at.desc())
        .limit(50)
    )
    approvals = result.scalars().all()
    return [
        ApprovalResponse(
            approval_id=a.approval_id,
            status=a.status,
            title=a.title,
            summary=a.summary,
            risk_level=a.risk_level,
            created_at=a.created_at,
        )
        for a in approvals
    ]


@router.post(
    "/v1/approvals/{approval_id}/approve",
    response_model=ApprovalResponse,
)
async def approve_action(
    approval_id: str,
    req: ApprovalDecisionRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Approve a pending action and trigger execution."""
    approval = await _get_approval(db, approval_id, user_id)

    approval.status = "approved"
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_reason = req.reason if req else None

    # Update execution status
    exec_result = await db.execute(
        select(Execution).where(Execution.execution_id == approval.execution_id)
    )
    execution = exec_result.scalar_one_or_none()
    if execution:
        execution.status = "pending"

    audit = AuditService(db)
    await audit.log(
        user_id=user_id,
        action_type="approval_approved",
        approval_id=approval_id,
        execution_id=approval.execution_id,
        summary=f"Approved: {approval.title}",
        details={"reason": req.reason if req else None},
    )

    await db.commit()

    # Trigger execution in background
    if execution:
        operator = Operator(settings=settings, db=db)
        try:
            await operator.execute_plan(execution.execution_id, user_id)
        except Exception:
            logger.exception(
                "Execution failed after approval: %s",
                execution.execution_id,
            )

    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
    )


@router.post(
    "/v1/approvals/{approval_id}/reject",
    response_model=ApprovalResponse,
)
async def reject_action(
    approval_id: str,
    req: ApprovalDecisionRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Reject a pending action."""
    approval = await _get_approval(db, approval_id, user_id)

    approval.status = "rejected"
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_reason = req.reason if req else None

    # Cancel the execution
    exec_result = await db.execute(
        select(Execution).where(Execution.execution_id == approval.execution_id)
    )
    execution = exec_result.scalar_one_or_none()
    if execution:
        execution.status = "cancelled"

    audit = AuditService(db)
    await audit.log(
        user_id=user_id,
        action_type="approval_rejected",
        approval_id=approval_id,
        execution_id=approval.execution_id,
        summary=f"Rejected: {approval.title}",
        details={"reason": req.reason if req else None},
    )

    await db.commit()

    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
    )


async def _get_approval(db: AsyncSession, approval_id: str, user_id: str) -> Approval:
    """Fetch an approval with row-level locking, raising 404 if not found or not pending.

    Uses SELECT ... FOR UPDATE to prevent concurrent approval race conditions.
    """
    result = await db.execute(
        select(Approval)
        .where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
        )
        .with_for_update()
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    if approval.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Approval already {approval.status}",
        )
    return approval
