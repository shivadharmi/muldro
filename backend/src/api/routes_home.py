"""API routes for the home feed — GET /v1/home."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id
from src.api.schemas.home import HomeResponse
from src.models.database import get_db

router = APIRouter(prefix="/v1/home")


@router.get("", response_model=HomeResponse)
async def get_home_feed(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.services.home_feed import HomeFeedService

    svc = HomeFeedService(db, workspace_id)
    data = await svc.build_home(user_id=user_id)
    return HomeResponse(**data)
