"""Approval endpoints — list, approve, and reject pending actions."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import ApprovalDecisionRequest, ApprovalDetailResponse, ApprovalResponse
from src.config.settings import Settings, get_settings
from src.models.approvals import Approval
from src.models.plans import Plan
from src.models.task_graph import TaskRun
from src.services.audit import AuditService
from src.services.operator import Operator

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/v1/approvals/{approval_id}", response_model=ApprovalDetailResponse)
async def get_approval_detail(
    approval_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get detailed info for a single approval, including execution and plan context."""
    result = await db.execute(
        select(Approval).where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
        )
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")

    # Get plan goal and trace_id via TaskRun
    plan_goal = None
    trace_id = None
    if approval.execution_id:
        run_result = await db.execute(
            select(TaskRun).where(TaskRun.run_id == approval.execution_id)
        )
        run = run_result.scalar_one_or_none()
        if run:
            trace_id = run.trace_id
            if run.plan_id:
                plan_result = await db.execute(select(Plan.goal).where(Plan.plan_id == run.plan_id))
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
        trace_id=trace_id,
    )


@router.get("/v1/approvals", response_model=list[ApprovalResponse])
async def list_approvals(
    status: str = "pending",
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List approvals for the user, filtered by status."""
    result = await db.execute(
        select(Approval)
        .where(
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
            Approval.status == status,
        )
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
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Approve a pending action and trigger execution."""
    approval = await _get_approval(db, approval_id, user_id, workspace_id)

    approval.status = "approved"
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_reason = req.reason if req else None

    # Update run status
    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == approval.execution_id))
    run = run_result.scalar_one_or_none()
    if run:
        run.status = "pending"

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

    # Publish approval.approved domain event via SSE
    try:
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        bus = EventBus(redis)
        stream = bus.agent_stream(user_id)
        await bus.publish(
            stream,
            "approval.approved",
            {"approval_id": approval_id, "run_id": approval.execution_id},
            user_id,
        )
        await redis.aclose()
    except Exception:
        logger.debug("Failed to publish approval.approved event", exc_info=True)

    # Resume the run (either step-level approval gate or plan-level)
    if approval.run_id:
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings=settings, db=db)
        try:
            from src.models.task_graph import TaskStep

            step_result = await db.execute(
                select(TaskStep).where(
                    TaskStep.step_id == approval.step_id,
                    TaskStep.run_id == approval.run_id,
                )
            )
            step = step_result.scalar_one_or_none()
            if step and step.status == "waiting_approval":
                step.status = "pending"
                await db.flush()
            await executor.resume_run(approval.run_id)
        except Exception:
            logger.exception("Resume failed after approval: %s", approval.run_id)
    elif run and run.plan_id:
        # Plan-level approval: trigger execution via Operator
        operator = Operator(settings=settings, db=db)
        try:
            await operator.execute_plan(run.run_id, user_id)
        except Exception:
            logger.exception("Execution failed after approval: %s", run.run_id)

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
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Reject a pending action."""
    approval = await _get_approval(db, approval_id, user_id, workspace_id)

    approval.status = "rejected"
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_reason = req.reason if req else None

    # Cancel the run
    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == approval.execution_id))
    run = run_result.scalar_one_or_none()
    if run:
        run.status = "cancelled"

    # If approval has a run_id, cancel the run
    if approval.run_id:
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings=settings, db=db)
        try:
            await executor.cancel_run(approval.run_id)
        except Exception:
            logger.warning("Failed to cancel run %s", approval.run_id, exc_info=True)

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

    # Publish approval.rejected domain event via SSE
    try:
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        bus = EventBus(redis)
        stream = bus.agent_stream(user_id)
        await bus.publish(
            stream,
            "approval.rejected",
            {"approval_id": approval_id, "run_id": approval.execution_id},
            user_id,
        )
        await redis.aclose()
    except Exception:
        logger.debug("Failed to publish approval.rejected event", exc_info=True)

    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
    )


class ApprovalEditRequest(BaseModel):
    title: str | None = None
    summary: str | None = None
    risk_level: str | None = None


@router.post(
    "/v1/approvals/{approval_id}/edit",
    response_model=ApprovalResponse,
)
async def edit_approval(
    approval_id: str,
    req: ApprovalEditRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Edit a pending approval's metadata before deciding."""
    result = await db.execute(
        select(Approval).where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
        )
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    if approval.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit approval in '{approval.status}' state",
        )

    if req.title is not None:
        approval.title = req.title
    if req.summary is not None:
        approval.summary = req.summary
    if req.risk_level is not None:
        approval.risk_level = req.risk_level

    await db.commit()

    return ApprovalResponse(
        approval_id=approval.approval_id,
        status=approval.status,
        title=approval.title,
        summary=approval.summary,
        risk_level=approval.risk_level,
        created_at=approval.created_at,
    )


async def _get_approval(
    db: AsyncSession, approval_id: str, user_id: str, workspace_id: str
) -> Approval:
    """Fetch an approval with row-level locking, raising 404 if not found or not pending.

    Uses SELECT ... FOR UPDATE to prevent concurrent approval race conditions.
    """
    result = await db.execute(
        select(Approval)
        .where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
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
