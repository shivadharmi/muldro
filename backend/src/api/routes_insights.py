"""Insight surface endpoints — dismiss and execute suggested actions."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.ui_state import UISurface

logger = logging.getLogger(__name__)

router = APIRouter()


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str | None = None


class DismissResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["dismissed"] = "dismissed"
    surface_id: str


@router.post(
    "/v1/insights/{surface_id}/dismiss",
    response_model=DismissResponse,
)
async def dismiss_insight(
    surface_id: str,
    body: DismissRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Dismiss a proactive insight surface and record in engagement history."""
    result = await db.execute(
        select(UISurface).where(
            UISurface.surface_id == surface_id,
            UISurface.user_id == user_id,
            UISurface.workspace_id == workspace_id,
            UISurface.surface_type == "proactive_insight",
        )
    )
    surface = result.scalar_one_or_none()
    if not surface:
        raise HTTPException(status_code=404, detail="Insight surface not found")

    # Record dismissal in engagement history
    payload = surface.payload or {}
    insight_data = payload.get("insight_data", {})
    signal_source = insight_data.get("signal_source", "unknown")
    signal_category = insight_data.get("signal_category", "unknown")

    from src.services.engagement_service import EngagementService

    eng_svc = EngagementService(db, workspace_id)
    await eng_svc.record_engagement(signal_source, signal_category, "dismissed")

    # Remove surface
    await db.delete(surface)
    await db.commit()

    return DismissResponse(surface_id=surface_id)
