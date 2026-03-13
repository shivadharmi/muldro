"""System endpoints — heartbeat, maintenance, diagnostics."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.config.settings import Settings, get_settings
from src.services.heartbeat import HeartbeatService

router = APIRouter()


class HeartbeatResponse(BaseModel):
    expired_memories: int = 0
    stale_plans_found: int = 0
    plans_escalated: int = 0
    timestamp: str


@router.post("/v1/system/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Run a heartbeat cycle: expire memories, escalate stale plans."""
    service = HeartbeatService(settings=settings, db=db)
    result = await service.run(user_id)
    await db.commit()
    return HeartbeatResponse(**result)
