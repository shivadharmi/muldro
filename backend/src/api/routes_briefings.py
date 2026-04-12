"""Briefing endpoints — daily briefing generation, list/detail/lifecycle."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import BriefingResponse
from src.config.settings import Settings, get_settings
from src.models.briefings import Briefing

router = APIRouter()


@router.get("/v1/briefings/{briefing_date}")
async def get_briefing(
    briefing_date: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Fetch or generate the daily briefing.

    If a briefing for this date exists, return it.
    Otherwise, return 202 Accepted to indicate async generation.
    """
    try:
        parsed_date = date.fromisoformat(briefing_date)
    except ValueError:
        parsed_date = date.today()

    # Check if briefing already exists for this date
    result = await db.execute(
        select(Briefing).where(
            Briefing.workspace_id == workspace_id,
            Briefing.briefing_date == parsed_date,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        return BriefingResponse(
            briefing_id=existing.briefing_id,
            date=existing.briefing_date,
            headline=existing.headline,
            top_priorities=existing.top_priorities or [],
            changes_since_last=existing.changes_since_last or [],
            pending_approvals=existing.pending_approvals or [],
            recommended_actions=existing.recommended_actions or [],
            full_text=existing.full_text,
        )

    # Briefing not ready — return 202 Accepted
    return JSONResponse(
        status_code=202,
        content={
            "status": "generating",
            "briefing_date": briefing_date,
            "message": "Briefing is being generated. Check back shortly.",
        },
    )


# ── Read-model endpoints ──────────────────────────────────────────


@router.get("/v1/briefings")
async def list_briefings(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List briefings with pagination and optional status filter."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    return await model.list_briefings(limit=limit, offset=offset, status=status)


@router.get("/v1/briefings/detail/{briefing_id}")
async def get_briefing_detail(
    briefing_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get full briefing detail with evidence and related items."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    detail = await model.get_detail(briefing_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return detail


@router.post("/v1/briefings/{briefing_id}/pin")
async def pin_briefing(
    briefing_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Pin a briefing for easy access."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    success = await model.pin_briefing(briefing_id)
    await db.commit()
    if not success:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return {"status": "pinned"}


@router.post("/v1/briefings/{briefing_id}/snooze")
async def snooze_briefing(
    briefing_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Snooze a briefing (hide temporarily)."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    success = await model.snooze_briefing(briefing_id)
    await db.commit()
    if not success:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return {"status": "snoozed"}


@router.post("/v1/briefings/{briefing_id}/archive")
async def archive_briefing(
    briefing_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Archive a briefing."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    success = await model.archive_briefing(briefing_id)
    await db.commit()
    if not success:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return {"status": "archived"}
