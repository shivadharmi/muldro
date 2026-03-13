"""Task endpoints — list and manage Jarvis tasks (plans)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.schemas import TaskResponse
from src.models.plans import Plan

router = APIRouter()


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
