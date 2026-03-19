"""Execution listing and detail routes — backed by TaskRun."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.models.plans import Plan
from src.models.task_graph import TaskRun

router = APIRouter()
logger = logging.getLogger(__name__)


class ExecutionItem(BaseModel):
    execution_id: str
    plan_id: str | None = None
    status: str
    source: str = "plan"
    execution_mode: str | None = None
    current_step_ids: list[str] | None = None
    error: dict | None = None
    goal: str | None = None
    priority: str | None = None
    created_at: str | None = None

    model_config = {"from_attributes": True}


async def _build_execution_item(run: TaskRun, db: AsyncSession) -> ExecutionItem:
    goal = None
    priority = None
    if run.plan_id:
        plan_result = await db.execute(
            select(Plan.goal, Plan.priority).where(Plan.plan_id == run.plan_id)
        )
        row = plan_result.one_or_none()
        if row:
            goal, priority = row
    return ExecutionItem(
        execution_id=run.run_id,
        plan_id=run.plan_id,
        status=run.status,
        source=run.source or "plan",
        execution_mode=run.execution_mode,
        current_step_ids=run.current_step_ids,
        error=run.error,
        goal=goal,
        priority=priority,
        created_at=run.created_at.isoformat() if run.created_at else None,
    )


@router.get("/v1/executions", response_model=list[ExecutionItem])
async def list_executions(
    status: str | None = Query(None),
    source: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """List executions (TaskRuns) for the current user."""
    stmt = select(TaskRun).where(TaskRun.user_id == user_id)

    if status:
        stmt = stmt.where(TaskRun.status == status)
    if source:
        stmt = stmt.where(TaskRun.source == source)

    stmt = stmt.order_by(TaskRun.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    # Batch-fetch plan goals for efficiency
    plan_ids = [r.plan_id for r in rows if r.plan_id]
    plan_map: dict[str, tuple[str | None, str | None]] = {}
    if plan_ids:
        plans_result = await db.execute(
            select(Plan.plan_id, Plan.goal, Plan.priority).where(Plan.plan_id.in_(plan_ids))
        )
        for pid, pgoal, ppriority in plans_result.all():
            plan_map[pid] = (pgoal, ppriority)

    return [
        ExecutionItem(
            execution_id=r.run_id,
            plan_id=r.plan_id,
            status=r.status,
            source=r.source or "plan",
            execution_mode=r.execution_mode,
            current_step_ids=r.current_step_ids,
            error=r.error,
            goal=plan_map.get(r.plan_id, (None, None))[0] if r.plan_id else None,
            priority=plan_map.get(r.plan_id, (None, None))[1] if r.plan_id else None,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.get("/v1/executions/{execution_id}", response_model=ExecutionItem)
async def get_execution(
    execution_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Get a single execution by ID."""
    result = await db.execute(
        select(TaskRun).where(TaskRun.run_id == execution_id, TaskRun.user_id == user_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Execution not found")

    return await _build_execution_item(run, db)
