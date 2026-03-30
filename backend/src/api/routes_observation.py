"""Perception health tracking endpoints.

Backed by PerceptionState — the single source of truth for observation scheduling,
health, and circuit breaker state per source.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import PerceptionReportRequest, PerceptionStatusResponse
from src.config.settings import Settings, get_settings
from src.models.ids import generate_id
from src.models.perception_state import PerceptionState

router = APIRouter()

STALE_THRESHOLDS_ATTR = {
    "gmail": "observation_stale_gmail_minutes",
    "calendar": "observation_stale_calendar_minutes",
    "github": "observation_stale_github_minutes",
}
DEFAULT_STALE_MINUTES = 60


@router.post("/v1/observations/report", response_model=PerceptionStatusResponse)
async def report_observation(
    req: PerceptionReportRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Upsert perception state after an observation cycle."""
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(PerceptionState).where(
            PerceptionState.user_id == user_id,
            PerceptionState.workspace_id == workspace_id,
            PerceptionState.source == req.source,
        )
    )
    ps = result.scalar_one_or_none()

    circuit = "open" if req.status == "error" else "closed"

    if ps:
        ps.last_run_at = now
        ps.last_event_count = req.event_count
        ps.circuit_state = circuit
        ps.last_error = req.error_message
        if req.status == "error":
            ps.consecutive_failures += 1
        else:
            ps.consecutive_failures = 0
        ps.total_runs += 1
    else:
        ps = PerceptionState(
            state_id=generate_id("pst"),
            user_id=user_id,
            workspace_id=workspace_id,
            source=req.source,
            last_run_at=now,
            last_event_count=req.event_count,
            circuit_state=circuit,
            last_error=req.error_message,
            consecutive_failures=1 if req.status == "error" else 0,
            total_runs=1,
        )
        db.add(ps)

    await db.commit()
    await db.refresh(ps)

    return PerceptionStatusResponse(
        source=ps.source,
        last_run_at=ps.last_run_at,
        event_count=ps.last_event_count,
        circuit_state=ps.circuit_state,
        error_message=ps.last_error,
        consecutive_failures=ps.consecutive_failures,
        total_runs=ps.total_runs,
        is_stale=_check_stale(ps, settings),
    )


@router.get("/v1/observations/status", response_model=list[PerceptionStatusResponse])
async def get_observation_status(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Return all perception sources with staleness computed."""
    result = await db.execute(
        select(PerceptionState).where(
            PerceptionState.user_id == user_id,
            PerceptionState.workspace_id == workspace_id,
        )
    )
    states = result.scalars().all()

    return [
        PerceptionStatusResponse(
            source=ps.source,
            last_run_at=ps.last_run_at,
            event_count=ps.last_event_count,
            circuit_state=ps.circuit_state,
            error_message=ps.last_error,
            consecutive_failures=ps.consecutive_failures,
            total_runs=ps.total_runs,
            is_stale=_check_stale(ps, settings),
        )
        for ps in states
    ]


def _check_stale(ps: PerceptionState, settings: Settings) -> bool:
    """Check if a perception source is stale based on configured thresholds."""
    if ps.circuit_state == "open":
        return True
    if ps.last_run_at is None:
        return True

    now = datetime.now(timezone.utc)
    attr_name = STALE_THRESHOLDS_ATTR.get(ps.source)
    if attr_name:
        stale_minutes = getattr(settings, attr_name, DEFAULT_STALE_MINUTES)
    else:
        stale_minutes = DEFAULT_STALE_MINUTES
    threshold = now - timedelta(minutes=stale_minutes)
    return ps.last_run_at < threshold
