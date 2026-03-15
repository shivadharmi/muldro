"""Observation health tracking endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.api.schemas import ObservationReportRequest, ObservationStatusResponse
from src.config.settings import Settings, get_settings
from src.models.observation import ObservationStatus

router = APIRouter()

STALE_THRESHOLDS_ATTR = {
    "gmail": "observation_stale_gmail_minutes",
    "calendar": "observation_stale_calendar_minutes",
    "github": "observation_stale_github_minutes",
}
DEFAULT_STALE_MINUTES = 60


@router.post("/v1/observations/report", response_model=ObservationStatusResponse)
async def report_observation(
    req: ObservationReportRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Upsert observation status after an observation cycle."""
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(ObservationStatus).where(
            ObservationStatus.user_id == user_id,
            ObservationStatus.source == req.source,
        )
    )
    obs = result.scalar_one_or_none()

    if obs:
        obs.last_observed_at = now
        obs.items_found = req.items_found
        obs.items_ingested = req.items_ingested
        obs.status = req.status
        obs.error_message = req.error_message
    else:
        obs = ObservationStatus(
            user_id=user_id,
            source=req.source,
            last_observed_at=now,
            items_found=req.items_found,
            items_ingested=req.items_ingested,
            status=req.status,
            error_message=req.error_message,
        )
        db.add(obs)

    await db.commit()
    await db.refresh(obs)

    is_stale = _check_stale(obs, settings)

    return ObservationStatusResponse(
        source=obs.source,
        last_observed_at=obs.last_observed_at,
        items_found=obs.items_found,
        items_ingested=obs.items_ingested,
        status=obs.status,
        error_message=obs.error_message,
        is_stale=is_stale,
    )


@router.get("/v1/observations/status", response_model=list[ObservationStatusResponse])
async def get_observation_status(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Return all observation sources with staleness computed."""
    result = await db.execute(select(ObservationStatus).where(ObservationStatus.user_id == user_id))
    observations = result.scalars().all()

    return [
        ObservationStatusResponse(
            source=obs.source,
            last_observed_at=obs.last_observed_at,
            items_found=obs.items_found,
            items_ingested=obs.items_ingested,
            status=obs.status,
            error_message=obs.error_message,
            is_stale=_check_stale(obs, settings),
        )
        for obs in observations
    ]


def _check_stale(obs: ObservationStatus, settings: Settings) -> bool:
    """Check if an observation source is stale based on configured thresholds."""
    if obs.status == "error":
        return True

    now = datetime.now(timezone.utc)
    attr_name = STALE_THRESHOLDS_ATTR.get(obs.source)
    if attr_name:
        stale_minutes = getattr(settings, attr_name, DEFAULT_STALE_MINUTES)
    else:
        stale_minutes = DEFAULT_STALE_MINUTES
    threshold = now - timedelta(minutes=stale_minutes)
    return obs.last_observed_at < threshold
