"""Task endpoints — standalone task management."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
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


@router.get("/v1/tasks", response_model=list[StandaloneTaskResponse])
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
    """List standalone tasks."""
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
    return [_task_response(t) for t in tasks]


@router.get("/v1/tasks/{task_id}")
async def get_task_detail(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get detailed info for a standalone task."""
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
