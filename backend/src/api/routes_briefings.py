"""Briefing endpoints — daily briefing generation and retrieval."""

from datetime import date

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.schemas import BriefingResponse

router = APIRouter()


@router.get("/v1/briefings/{briefing_date}", response_model=BriefingResponse)
async def get_briefing(
    briefing_date: str,
    user_id: str = Depends(get_current_user),
):
    """Fetch or generate the daily briefing.

    If a briefing for this date exists, return it.
    Otherwise, trigger generation from recent events.
    """
    try:
        parsed_date = date.fromisoformat(briefing_date)
    except ValueError:
        parsed_date = date.today()

    # TODO: Wire to briefing service
    return BriefingResponse(
        briefing_id=f"brief_{parsed_date.isoformat()}",
        date=parsed_date,
        headline="Jarvis briefing service not yet connected.",
        top_priorities=[],
        changes_since_last=[],
        pending_approvals=[],
        recommended_actions=["Connect Gmail and Calendar connectors to enable briefings."],
    )
