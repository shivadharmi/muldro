"""Approval endpoints — list, approve, and reject pending actions."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import ApprovalDecisionRequest, ApprovalDetailResponse, ApprovalResponse
from src.config.settings import Settings, get_settings
from src.models.approvals import Approval
from src.models.plans import Plan
from src.models.task_graph import TaskRun, TaskStep
from src.services.audit import AuditService

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
    approval.approved_by = user_id

    # Resolve the linked run for event emission and downstream resume.
    # Do NOT transition the run here — resume_run() and execute_run()
    # handle their own state transitions after re-reading the row.
    effective_run_id = approval.run_id or approval.execution_id
    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == effective_run_id))
    run = run_result.scalar_one_or_none()

    # Emit approval_resolved runtime event
    try:
        from src.models.runtime_event import RuntimeEvent

        db.add(
            RuntimeEvent(
                workspace_id=workspace_id,
                run_id=approval.run_id,
                step_id=approval.step_id,
                event_type="approval_resolved",
                payload={
                    "approval_id": approval_id,
                    "decision": "approved",
                    "reason": req.reason if req else None,
                },
            )
        )
    except Exception:
        pass

    audit = AuditService(db)
    await audit.log(
        user_id=user_id,
        action_type="approval_approved",
        workspace_id=workspace_id,
        approval_id=approval_id,
        execution_id=approval.execution_id,
        summary=f"Approved: {approval.title}",
        details={"reason": req.reason if req else None},
    )

    # Trust feedback loop — record approval for graduated autonomy
    try:
        from src.services.risk_assessor import record_approval_decision

        capability = approval.approval_type
        if ":" in capability:
            capability = capability.split(":", 1)[1]
        decision_type = "modified" if req and req.reason else "approved"
        await record_approval_decision(
            db, workspace_id, capability, approval.risk_level or "low", decision_type
        )
    except Exception:
        logger.warning("Trust feedback failed for approval %s", approval_id, exc_info=True)

    await db.commit()

    # Embed approval decision into Qdrant
    try:
        from src.services.embedding_service import EmbeddingService
        from src.services.vector_store import VectorStore

        if settings.qdrant_url:
            vs = VectorStore(settings)
            es = EmbeddingService(settings)
            await _embed_approval_decision(
                approval_id=approval_id,
                approval_type=approval.approval_type or "",
                summary=approval.summary or "",
                risk_level=approval.risk_level or "low",
                outcome="approved",
                user_id=user_id,
                embedding_service=es,
                vector_store=vs,
            )
    except Exception:
        logger.debug("Approval embedding failed", exc_info=True)

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
        from src.services.graph_executor import create_graph_executor

        executor = await create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)
        try:
            from src.models.task_graph import TaskStep

            step_result = await db.execute(
                select(TaskStep).where(
                    TaskStep.step_id == approval.step_id,
                    TaskStep.run_id == approval.run_id,
                )
            )
            from src.services.execution_state import transition_step

            step = step_result.scalar_one_or_none()
            if step and step.status == "waiting_approval":
                transition_step(step, "running")
                await db.flush()
            await executor.resume_run(approval.run_id)
        except Exception:
            logger.exception("Resume failed after approval: %s", approval.run_id)
    elif run and run.plan_id:
        # Plan-level approval: trigger execution via GraphExecutor directly
        try:
            executor = await create_graph_executor(
                settings=settings, db=db, workspace_id=workspace_id
            )
            await executor.execute_run(run.run_id)
        except Exception:
            logger.exception("Execution failed after approval: %s", run.run_id)
    elif approval.artifact_refs and approval.artifact_refs.get("tool_name"):
        # B1: Tool-level approval resume — create a background TaskRun to
        # re-execute the approved tool with the original parameters.
        try:
            from src.models.plans import Plan, PlanTask

            tool_name = approval.artifact_refs["tool_name"]
            tool_params = approval.artifact_refs.get("tool_params", {})
            plan_id = f"plan_{ULID()}"
            plan = Plan(
                plan_id=plan_id,
                user_id=user_id,
                workspace_id=workspace_id,
                trigger_type="approval_resume",
                goal=f"Execute approved tool: {tool_name}",
                priority="high",
                decision="create_task",
                status="created",
            )
            plan.tasks = [
                PlanTask(
                    task_id=f"ptask_{ULID()}",
                    plan_id=plan_id,
                    workspace_id=workspace_id,
                    task_type=tool_name,
                    input_data=tool_params,
                    status="pending",
                )
            ]
            db.add(plan)

            bg_run = TaskRun(
                run_id=f"run_{ULID()}",
                plan_id=plan_id,
                user_id=user_id,
                workspace_id=workspace_id,
                source="approval_resume",
                status="pending",
                trace_id=None,
            )
            db.add(bg_run)
            await db.commit()
            logger.info(
                "Tool-level approval resumed: %s → run %s",
                approval.approval_id,
                bg_run.run_id,
            )
        except Exception:
            logger.exception(
                "Failed to create resume run for tool approval: %s",
                approval.approval_id,
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
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Reject a pending action."""
    approval = await _get_approval(db, approval_id, user_id, workspace_id)

    from src.services.execution_state import transition_run

    approval.status = "rejected"
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_reason = req.reason if req else None
    approval.approved_by = user_id

    # Emit approval_resolved runtime event
    try:
        from src.models.runtime_event import RuntimeEvent

        db.add(
            RuntimeEvent(
                workspace_id=workspace_id,
                run_id=approval.run_id,
                step_id=approval.step_id,
                event_type="approval_resolved",
                payload={
                    "approval_id": approval_id,
                    "decision": "rejected",
                    "reason": req.reason if req else None,
                },
            )
        )
    except Exception:
        pass

    # Cancel the run and transition the step
    effective_run_id = approval.run_id or approval.execution_id
    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == effective_run_id))
    run = run_result.scalar_one_or_none()
    if run and run.status in ("awaiting_approval", "running", "paused"):
        # Transition the waiting step to cancelled
        if approval.step_id:
            from src.services.execution_state import transition_step

            step_result = await db.execute(
                select(TaskStep).where(
                    TaskStep.step_id == approval.step_id,
                    TaskStep.run_id == effective_run_id,
                )
            )
            step = step_result.scalar_one_or_none()
            if step and step.status == "waiting_approval":
                transition_step(step, "cancelled")

        # Cancel the full run via graph executor (handles all remaining steps)
        from src.services.graph_executor import create_graph_executor

        executor = await create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)
        try:
            await executor.cancel_run(effective_run_id)
        except Exception:
            # Fallback: direct state transition
            transition_run(run, "cancelled")
            logger.warning("Failed to cancel run %s via executor", effective_run_id, exc_info=True)

    audit = AuditService(db)
    await audit.log(
        user_id=user_id,
        action_type="approval_rejected",
        workspace_id=workspace_id,
        approval_id=approval_id,
        execution_id=approval.execution_id,
        summary=f"Rejected: {approval.title}",
        details={"reason": req.reason if req else None},
    )

    # Trust feedback loop — record rejection for graduated autonomy
    try:
        from src.services.risk_assessor import record_approval_decision

        capability = approval.approval_type
        if ":" in capability:
            capability = capability.split(":", 1)[1]
        await record_approval_decision(
            db, workspace_id, capability, approval.risk_level or "low", "rejected"
        )
    except Exception:
        logger.warning("Trust feedback failed for rejection %s", approval_id, exc_info=True)

    await db.commit()

    # Embed rejection decision into Qdrant
    try:
        from src.services.embedding_service import EmbeddingService
        from src.services.vector_store import VectorStore

        if settings.qdrant_url:
            vs = VectorStore(settings)
            es = EmbeddingService(settings)
            await _embed_approval_decision(
                approval_id=approval_id,
                approval_type=approval.approval_type or "",
                summary=approval.summary or "",
                risk_level=approval.risk_level or "low",
                outcome="rejected",
                user_id=user_id,
                embedding_service=es,
                vector_store=vs,
            )
    except Exception:
        logger.debug("Rejection embedding failed", exc_info=True)

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


@router.get("/v1/approvals/{approval_id}/impact")
async def get_approval_impact(
    approval_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get impact analysis for a pending approval."""
    from src.services.approval_impact import ApprovalImpactService

    svc = ApprovalImpactService(db, workspace_id)
    impact = await svc.get_impact(approval_id)
    affected = await svc.get_affected_entities(approval_id)

    return {
        "approval_id": approval_id,
        "risk_level": impact.risk_level,
        "reversibility": impact.reversibility,
        "reversibility_detail": impact.reversibility_detail,
        "policy_explanation": impact.policy_explanation,
        "downstream_effects": impact.downstream_effects,
        "affected_entities": [
            {
                "entity_id": e.entity_id,
                "name": e.name,
                "entity_type": e.entity_type,
                "impact_type": e.impact_type,
            }
            for e in affected
        ],
    }


async def _embed_approval_decision(
    approval_id: str,
    approval_type: str,
    summary: str,
    risk_level: str,
    outcome: str,
    user_id: str,
    embedding_service,
    vector_store,
) -> None:
    """Embed approval decision into Qdrant (best-effort)."""
    try:
        text = f"{approval_type}: {summary} → {outcome}"
        embedding = await embedding_service.embed_text(text)
        if embedding:
            await vector_store.upsert(
                collection="approvals",
                id=approval_id,
                vector=embedding,
                payload={
                    "capability": approval_type,
                    "risk_level": risk_level,
                    "outcome": outcome,
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                },
                user_id=user_id,
            )
    except Exception:
        logger.debug("Approval embedding failed for %s", approval_id, exc_info=True)


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
