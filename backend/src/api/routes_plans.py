"""Plan API routes — plan detail for internal tool use."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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
