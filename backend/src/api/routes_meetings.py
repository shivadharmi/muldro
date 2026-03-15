"""Meeting prep endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.api.schemas import MeetingPrepRequest, MeetingPrepResponse
from src.config.settings import Settings, get_settings
from src.services.presenter import Presenter

router = APIRouter()


@router.post("/v1/meetings/prep", response_model=MeetingPrepResponse)
async def meeting_prep(
    req: MeetingPrepRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Generate meeting preparation for an upcoming event."""
    presenter = Presenter(settings=settings, db=db)
    result = await presenter.generate_meeting_prep(
        meeting_id=req.meeting_id,
        user_id=user_id,
        next_meeting=req.next or False,
    )
    return MeetingPrepResponse(
        meeting_id=result.get("meeting_id", req.meeting_id or "none"),
        title=result.get("title", "Unknown"),
        starts_at=result.get("starts_at"),
        attendees=result.get("attendees", []),
        agenda=result.get("agenda", []),
        related_threads=result.get("related_threads", []),
        action_items=result.get("action_items", []),
        risks=result.get("risks", []),
    )
