"""Task endpoints — list and manage Jarvis tasks."""

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.schemas import TaskResponse

router = APIRouter()


@router.get("/v1/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = None,
    limit: int = 10,
    user_id: str = Depends(get_current_user),
):
    """List tasks for the current user, optionally filtered by status."""
    # TODO: Wire to task/plan service
    return []
