"""Plan API routes — list, detail, and run history for plans."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PlanTaskResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    depends_on: list[str] | None = None


class PlanSummaryResponse(BaseModel):
    plan_id: str
    goal: str
    priority: str
    status: str
    risk_level: str
    trigger_type: str
    task_count: int = 0
    created_at: str


class PlanDetailResponse(PlanSummaryResponse):
    reasoning_summary: str | None = None
    execution_mode: str
    success_conditions: dict | None = None
    plan_output_json: dict | None = None
    tasks: list[PlanTaskResponse] = []


class PlanRunResponse(BaseModel):
    run_id: str
    status: str
    source: str
    started_at: str | None = None
    completed_at: str | None = None
    error: dict | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/v1/plans", response_model=list[PlanSummaryResponse])
async def list_plans(
    status: str | None = None,
    trigger_type: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db=Depends(get_session),
):
    """List plans with optional filters."""
    from src.models.plans import Plan, PlanTask

    # Build a subquery for task counts
    task_count_sq = (
        select(PlanTask.plan_id, func.count().label("task_count"))
        .group_by(PlanTask.plan_id)
        .subquery()
    )

    stmt = (
        select(Plan, func.coalesce(task_count_sq.c.task_count, 0).label("task_count"))
        .outerjoin(task_count_sq, Plan.plan_id == task_count_sq.c.plan_id)
        .where(
            Plan.user_id == user_id,
            Plan.workspace_id == workspace_id,
        )
    )

    if status:
        stmt = stmt.where(Plan.status == status)
    if trigger_type:
        stmt = stmt.where(Plan.trigger_type == trigger_type)
    if created_after:
        stmt = stmt.where(Plan.created_at >= created_after)
    if created_before:
        stmt = stmt.where(Plan.created_at <= created_before)

    stmt = stmt.order_by(Plan.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        PlanSummaryResponse(
            plan_id=plan.plan_id,
            goal=plan.goal,
            priority=plan.priority,
            status=plan.status,
            risk_level=plan.risk_level,
            trigger_type=plan.trigger_type,
            task_count=task_count,
            created_at=plan.created_at.isoformat() if plan.created_at else "",
        )
        for plan, task_count in rows
    ]


@router.get("/v1/plans/{plan_id}", response_model=PlanDetailResponse)
async def get_plan(
    plan_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db=Depends(get_session),
):
    """Get full plan detail with tasks and plan_output_json."""
    from src.models.plans import Plan, PlanTask

    result = await db.execute(
        select(Plan).where(
            Plan.plan_id == plan_id,
            Plan.user_id == user_id,
            Plan.workspace_id == workspace_id,
        )
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    tasks_result = await db.execute(select(PlanTask).where(PlanTask.plan_id == plan_id))
    tasks = tasks_result.scalars().all()

    return PlanDetailResponse(
        plan_id=plan.plan_id,
        goal=plan.goal,
        priority=plan.priority,
        status=plan.status,
        risk_level=plan.risk_level,
        trigger_type=plan.trigger_type,
        task_count=len(tasks),
        created_at=plan.created_at.isoformat() if plan.created_at else "",
        reasoning_summary=plan.reasoning_summary,
        execution_mode=plan.execution_mode,
        success_conditions=plan.success_conditions,
        plan_output_json=plan.plan_output_json,
        tasks=[
            PlanTaskResponse(
                task_id=t.task_id,
                task_type=t.task_type,
                status=t.status,
                depends_on=t.depends_on if isinstance(t.depends_on, list) else None,
            )
            for t in tasks
        ],
    )


@router.get("/v1/plans/{plan_id}/runs", response_model=list[PlanRunResponse])
async def get_plan_runs(
    plan_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db=Depends(get_session),
):
    """Get all execution runs for a plan."""
    from src.models.plans import Plan
    from src.models.task_graph import TaskRun

    # Verify plan exists and belongs to user/workspace
    plan_exists = await db.execute(
        select(Plan.plan_id).where(
            Plan.plan_id == plan_id,
            Plan.user_id == user_id,
            Plan.workspace_id == workspace_id,
        )
    )
    if not plan_exists.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Plan not found")

    result = await db.execute(
        select(TaskRun)
        .where(
            TaskRun.plan_id == plan_id,
            TaskRun.workspace_id == workspace_id,
        )
        .order_by(TaskRun.created_at.desc())
    )
    runs = result.scalars().all()

    return [
        PlanRunResponse(
            run_id=r.run_id,
            status=r.status,
            source=r.source,
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            error=r.error,
        )
        for r in runs
    ]
