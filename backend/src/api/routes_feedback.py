"""Briefing feedback endpoints — learning loop for briefing quality."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_session
from src.api.schemas import (
    BriefingFeedbackRequest,
    BriefingFeedbackResponse,
    BriefingFeedbackSummary,
)
from src.models.briefing_feedback import BriefingFeedback
from src.models.briefings import Briefing

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/v1/briefings/{briefing_id}/feedback",
    response_model=BriefingFeedbackResponse,
)
async def submit_briefing_feedback(
    briefing_id: str,
    req: BriefingFeedbackRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Record user feedback on a briefing or briefing item."""
    # Validate briefing exists
    result = await db.execute(
        select(Briefing.briefing_id).where(
            Briefing.briefing_id == briefing_id,
            Briefing.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Briefing not found")

    valid_types = {"rating", "item_acted_on", "item_dismissed", "follow_up_asked"}
    if req.feedback_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"feedback_type must be one of: {', '.join(sorted(valid_types))}",
        )

    if req.feedback_type == "rating" and (req.rating is None or not 1 <= req.rating <= 5):
        raise HTTPException(status_code=400, detail="rating must be 1-5 for feedback_type=rating")

    # Compute signal weight: ratings and actions weigh more than dismissals
    weight = 1.0
    if req.feedback_type == "rating":
        weight = 2.0
    elif req.feedback_type == "item_acted_on":
        weight = 1.5

    feedback_id = f"bfb_{ULID()}"
    feedback = BriefingFeedback(
        feedback_id=feedback_id,
        briefing_id=briefing_id,
        user_id=user_id,
        feedback_type=req.feedback_type,
        rating=req.rating,
        item_section=req.item_section,
        item_index=req.item_index,
        item_title=req.item_title,
        comment=req.comment,
        extra_data=req.extra_data,
        signal_weight=weight,
    )
    db.add(feedback)
    await db.commit()

    logger.info(
        "Briefing feedback recorded: %s type=%s briefing=%s",
        feedback_id,
        req.feedback_type,
        briefing_id,
    )

    return BriefingFeedbackResponse(
        feedback_id=feedback_id,
        briefing_id=briefing_id,
        feedback_type=req.feedback_type,
    )


@router.get(
    "/v1/briefings/{briefing_id}/feedback",
    response_model=BriefingFeedbackSummary,
)
async def get_briefing_feedback_summary(
    briefing_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Get aggregated feedback summary for a briefing."""
    result = await db.execute(
        select(
            func.count(BriefingFeedback.feedback_id).label("total"),
            func.avg(BriefingFeedback.rating).label("avg_rating"),
            func.count(BriefingFeedback.feedback_id)
            .filter(BriefingFeedback.feedback_type == "item_acted_on")
            .label("acted"),
            func.count(BriefingFeedback.feedback_id)
            .filter(BriefingFeedback.feedback_type == "item_dismissed")
            .label("dismissed"),
            func.count(BriefingFeedback.feedback_id)
            .filter(BriefingFeedback.feedback_type == "follow_up_asked")
            .label("follow_ups"),
        ).where(
            BriefingFeedback.briefing_id == briefing_id,
            BriefingFeedback.user_id == user_id,
        )
    )
    row = result.one()

    return BriefingFeedbackSummary(
        briefing_id=briefing_id,
        total_feedback=row.total or 0,
        average_rating=round(float(row.avg_rating), 1) if row.avg_rating else None,
        items_acted_on=row.acted or 0,
        items_dismissed=row.dismissed or 0,
        follow_ups_asked=row.follow_ups or 0,
    )
