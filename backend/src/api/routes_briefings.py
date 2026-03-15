"""Briefing endpoints — daily briefing generation and retrieval."""

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.api.schemas import BriefingResponse
from src.config.settings import Settings, get_settings
from src.services.presenter import Presenter

router = APIRouter()


@router.get("/v1/briefings/{briefing_date}", response_model=BriefingResponse)
async def get_briefing(
    briefing_date: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Fetch or generate the daily briefing.

    If a briefing for this date exists, return it.
    Otherwise, trigger generation from recent events.
    """
    try:
        parsed_date = date.fromisoformat(briefing_date)
    except ValueError:
        parsed_date = date.today()

    presenter = Presenter(settings=settings, db=db)
    briefing = await presenter.generate_briefing(user_id, parsed_date)

    return BriefingResponse(
        briefing_id=briefing.briefing_id,
        date=briefing.briefing_date,
        headline=briefing.headline,
        top_priorities=briefing.top_priorities or [],
        changes_since_last=briefing.changes_since_last or [],
        pending_approvals=briefing.pending_approvals or [],
        recommended_actions=briefing.recommended_actions or [],
        full_text=briefing.full_text,
    )
