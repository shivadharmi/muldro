"""Task endpoints — standalone tasks and legacy plan-based tasks."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import TaskDetailResponse, TaskResponse, TaskStepResponse
from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskRun, TaskStep
from src.models.tasks import Task
from src.services.task_service import TaskService

router = APIRouter()


# ── Standalone Task Schemas ──────────────────────────────────────


class StandaloneTaskCreateRequest(BaseModel):
    title: str
    description: str | None = None
    task_type: str = "general"
    priority: str = "medium"
    goal_id: str | None = None
    parent_task_id: str | None = None
    due_at: datetime | None = None
    metadata_json: dict | None = None


class StandaloneTaskResponse(BaseModel):
    task_id: str
    title: str
    description: str | None = None
    task_type: str
    source: str
    priority: str
    status: str
    goal_id: str | None = None
    parent_task_id: str | None = None
    due_at: datetime | None = None
    assigned_agent: str | None = None
    created_at: datetime | None = None


class AddDependencyRequest(BaseModel):
    depends_on_task_id: str
    dependency_type: str = "blocks"


# ── Standalone Task Endpoints ────────────────────────────────────


@router.post("/v1/tasks", response_model=StandaloneTaskResponse)
async def create_task(
    req: StandaloneTaskCreateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Create a standalone task."""
    svc = TaskService(db)
    task = await svc.create_task(
        user_id=user_id,
        workspace_id=workspace_id,
        title=req.title,
        description=req.description,
        task_type=req.task_type,
        priority=req.priority,
        goal_id=req.goal_id,
        parent_task_id=req.parent_task_id,
        due_at=req.due_at,
        metadata_json=req.metadata_json,
    )
    await db.commit()
    return _task_response(task)


@router.get("/v1/tasks", response_model=list[StandaloneTaskResponse | TaskResponse])
async def list_tasks(
    status: str | None = None,
    goal_id: str | None = None,
    task_type: str | None = None,
    priority: str | None = None,
    limit: int = 50,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List tasks — returns both standalone tasks and legacy plan-based tasks."""
    results: list = []

    # Standalone tasks
    svc = TaskService(db)
    tasks = await svc.list_tasks(
        user_id=user_id,
        workspace_id=workspace_id,
        status=status,
        goal_id=goal_id,
        task_type=task_type,
        priority=priority,
        limit=limit,
    )
    results.extend([_task_response(t) for t in tasks])

    # Legacy plan-based tasks (for backward compat)
    stmt = select(Plan).where(Plan.user_id == user_id, Plan.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(Plan.status == status)
    stmt = stmt.order_by(Plan.created_at.desc()).limit(limit)
    plan_result = await db.execute(stmt)
    for p in plan_result.scalars().all():
        results.append(
            TaskResponse(
                task_id=p.plan_id,
                goal=p.goal,
                priority=p.priority,
                status=p.status,
                decision=p.decision,
                created_at=p.created_at,
            )
        )

    return results[:limit]


@router.get("/v1/tasks/{task_id}")
async def get_task_detail(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get detailed info for a task (standalone or plan-based)."""
    # Route standalone tasks by prefix
    if task_id.startswith("task_"):
        svc = TaskService(db)
        task = await svc.get_task(task_id, user_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        deps = await svc.get_dependencies(task_id)
        resp = _task_response(task)
        return {
            **resp.model_dump(),
            "dependencies": [
                {
                    "depends_on_task_id": d.depends_on_task_id,
                    "dependency_type": d.dependency_type,
                }
                for d in deps
            ],
        }

    # Fall back to legacy plan-based task
    result = await db.execute(
        select(Plan).where(
            Plan.plan_id == task_id, Plan.user_id == user_id, Plan.workspace_id == workspace_id
        )
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    steps_result = await db.execute(select(PlanTask).where(PlanTask.plan_id == plan.plan_id))
    plan_tasks = steps_result.scalars().all()

    # Look up the latest TaskRun for this plan
    run_result = await db.execute(
        select(TaskRun)
        .where(TaskRun.plan_id == plan.plan_id)
        .order_by(TaskRun.created_at.desc())
        .limit(1)
    )
    task_run = run_result.scalar_one_or_none()

    steps = []
    step_map: dict[str, TaskStep] = {}
    if task_run:
        steps_result = await db.execute(select(TaskStep).where(TaskStep.run_id == task_run.run_id))
        for step in steps_result.scalars().all():
            if step.plan_task_id:
                step_map[step.plan_task_id] = step
            step_map[step.task_id] = step

    for pt in plan_tasks:
        step = step_map.get(pt.task_id)
        result_summary = None
        task_status = pt.status
        if step:
            task_status = step.status
            if step.output_data and isinstance(step.output_data, dict):
                result_summary = step.output_data.get("summary")
            if step.error and isinstance(step.error, dict):
                result_summary = f"Error: {step.error.get('message', '')}"
        steps.append(
            TaskStepResponse(
                task_id=pt.task_id,
                task_type=pt.task_type,
                status=task_status,
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
        execution_status=task_run.status if task_run else None,
        created_at=plan.created_at,
    )


@router.post("/v1/tasks/{task_id}/start", response_model=StandaloneTaskResponse)
async def start_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Start a standalone task (transitions to planning)."""
    svc = TaskService(db)
    try:
        task = await svc.start_task(task_id, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return _task_response(task)


@router.post("/v1/tasks/{task_id}/cancel", response_model=StandaloneTaskResponse)
async def cancel_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Cancel a standalone task."""
    svc = TaskService(db)
    try:
        task = await svc.cancel_task(task_id, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return _task_response(task)


@router.post("/v1/tasks/{task_id}/resume", response_model=StandaloneTaskResponse)
async def resume_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Resume a blocked/failed task."""
    svc = TaskService(db)
    task = await svc.get_task(task_id, user_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    try:
        task = await svc.transition(task_id, user_id, "queued")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return _task_response(task)


@router.post("/v1/tasks/{task_id}/dependencies")
async def add_dependency(
    task_id: str,
    req: AddDependencyRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Add a dependency to a task."""
    svc = TaskService(db)
    try:
        dep = await svc.add_dependency(
            task_id, req.depends_on_task_id, req.dependency_type, workspace_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {
        "task_id": dep.task_id,
        "depends_on_task_id": dep.depends_on_task_id,
        "dependency_type": dep.dependency_type,
    }


def _task_response(task: Task) -> StandaloneTaskResponse:
    return StandaloneTaskResponse(
        task_id=task.task_id,
        title=task.title,
        description=task.description,
        task_type=task.task_type,
        source=task.source,
        priority=task.priority,
        status=task.status,
        goal_id=task.goal_id,
        parent_task_id=task.parent_task_id,
        due_at=task.due_at,
        assigned_agent=task.assigned_agent,
        created_at=task.created_at,
    )
