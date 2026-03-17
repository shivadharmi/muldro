"""Goal CRUD routes."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.goals import Goal

router = APIRouter()
logger = logging.getLogger(__name__)


class GoalItem(BaseModel):
    goal_id: str
    title: str
    description: str | None = None
    target_date: str | None = None
    priority: str = "medium"
    status: str = "active"
    progress: float = 0.0
    success_criteria_json: dict | None = None
    created_at: str | None = None

    model_config = {"from_attributes": True}


class GoalListResponse(BaseModel):
    goals: list[GoalItem]


class GoalCreateRequest(BaseModel):
    title: str
    description: str | None = None
    target_date: datetime | None = None
    priority: str = "medium"
    success_criteria_json: dict | None = None


class GoalPatchRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    target_date: datetime | None = None
    priority: str | None = None
    status: str | None = None
    progress: float | None = None
    success_criteria_json: dict | None = None


def _to_item(g: Goal) -> GoalItem:
    return GoalItem(
        goal_id=g.goal_id,
        title=g.title,
        description=g.description,
        target_date=g.target_date.isoformat() if g.target_date else None,
        priority=g.priority,
        status=g.status,
        progress=g.progress,
        success_criteria_json=g.success_criteria_json,
        created_at=g.created_at.isoformat() if g.created_at else None,
    )


@router.post("/v1/goals", response_model=GoalItem, status_code=201)
async def create_goal(
    req: GoalCreateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Create a new goal."""
    goal = Goal(
        goal_id=f"goal_{ULID()}",
        user_id=user_id,
        workspace_id=workspace_id,
        title=req.title,
        description=req.description,
        target_date=req.target_date,
        priority=req.priority,
        success_criteria_json=req.success_criteria_json,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return _to_item(goal)


@router.get("/v1/goals", response_model=GoalListResponse)
async def list_goals(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List goals for the current user, optionally filtered by status."""
    stmt = select(Goal).where(Goal.user_id == user_id, Goal.workspace_id == workspace_id)

    if status:
        stmt = stmt.where(Goal.status == status)

    stmt = stmt.order_by(Goal.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return GoalListResponse(goals=[_to_item(g) for g in rows])


@router.get("/v1/goals/{goal_id}", response_model=GoalItem)
async def get_goal(
    goal_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get a single goal by ID."""
    result = await db.execute(
        select(Goal).where(
            Goal.goal_id == goal_id, Goal.user_id == user_id, Goal.workspace_id == workspace_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    return _to_item(goal)


@router.patch("/v1/goals/{goal_id}", response_model=GoalItem)
async def patch_goal(
    goal_id: str,
    req: GoalPatchRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Update goal fields (partial)."""
    result = await db.execute(
        select(Goal).where(
            Goal.goal_id == goal_id, Goal.user_id == user_id, Goal.workspace_id == workspace_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    for field, value in req.model_dump(exclude_none=True).items():
        setattr(goal, field, value)

    await db.commit()
    await db.refresh(goal)
    return _to_item(goal)


@router.delete("/v1/goals/{goal_id}", status_code=204)
async def delete_goal(
    goal_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Delete a goal."""
    result = await db.execute(
        select(Goal).where(
            Goal.goal_id == goal_id, Goal.user_id == user_id, Goal.workspace_id == workspace_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    await db.delete(goal)
    await db.commit()
