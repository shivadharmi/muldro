"""Task endpoints — list and manage Jarvis tasks (plans)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.schemas import TaskDetailResponse, TaskResponse, TaskStepResponse
from src.models.executions import Execution, ExecutionTaskRun
from src.models.plans import Plan, PlanTask

router = APIRouter()


@router.get("/v1/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task_detail(
    task_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get detailed info for a single task, including execution steps and progress."""
    result = await db.execute(select(Plan).where(Plan.plan_id == task_id, Plan.user_id == user_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Get plan tasks (steps)
    steps_result = await db.execute(select(PlanTask).where(PlanTask.plan_id == plan.plan_id))
    plan_tasks = steps_result.scalars().all()

    # Get execution status and task run results
    exec_result = await db.execute(
        select(Execution)
        .where(Execution.plan_id == plan.plan_id)
        .order_by(Execution.created_at.desc())
        .limit(1)
    )
    execution = exec_result.scalar_one_or_none()

    # Build step responses with run results
    steps = []
    task_run_map: dict[str, ExecutionTaskRun] = {}
    if execution:
        runs_result = await db.execute(
            select(ExecutionTaskRun).where(ExecutionTaskRun.execution_id == execution.execution_id)
        )
        for run in runs_result.scalars().all():
            task_run_map[run.task_id] = run

    for pt in plan_tasks:
        run = task_run_map.get(pt.task_id)
        result_summary = None
        status = pt.status
        if run:
            status = run.status
            if run.result_data and isinstance(run.result_data, dict):
                result_summary = run.result_data.get("summary")
            if run.error_message:
                result_summary = f"Error: {run.error_message}"
        steps.append(
            TaskStepResponse(
                task_id=pt.task_id,
                task_type=pt.task_type,
                status=status,
                result_summary=result_summary,
            )
        )

    return TaskDetailResponse(
        task_id=plan.plan_id,
        goal=plan.goal,
        priority=plan.priority,
        status=plan.status,
        decision=plan.decision,
        risk_level=plan.risk_level,
        reasoning_summary=plan.reasoning_summary,
        steps=steps,
        execution_status=execution.status if execution else None,
        created_at=plan.created_at,
    )


@router.get("/v1/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = None,
    limit: int = 10,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """List tasks (plans) for the current user, optionally filtered by status."""
    stmt = select(Plan).where(Plan.user_id == user_id)
    if status:
        stmt = stmt.where(Plan.status == status)
    stmt = stmt.order_by(Plan.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    plans = result.scalars().all()

    return [
        TaskResponse(
            task_id=p.plan_id,
            goal=p.goal,
            priority=p.priority,
            status=p.status,
            decision=p.decision,
            created_at=p.created_at,
        )
        for p in plans
    ]
