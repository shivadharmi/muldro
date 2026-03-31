"""REST endpoints for fetching A2UI surface state.

Provides endpoints for the frontend to fetch the latest surface payloads
when WebSocket is not available or on initial page load.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session

router = APIRouter()
logger = logging.getLogger(__name__)


class UISurfaceResponse(BaseModel):
    surface_id: str
    surface_type: str
    payload: dict
    created_at: str
    updated_at: str


class UISurfaceListResponse(BaseModel):
    surfaces: list[UISurfaceResponse]
    count: int


@router.get("/v1/ui/surfaces", response_model=UISurfaceListResponse)
async def get_user_surfaces(
    surface_type: str = "",
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """Get latest A2UI surfaces for the authenticated user.

    Optionally filter by surface_type (briefing, approval, dashboard).
    """
    from src.models.database import get_session_factory
    from src.models.ui_state import UISurface

    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        stmt = select(UISurface).where(
            UISurface.user_id == user_id,
            UISurface.workspace_id == workspace_id,
            UISurface.expires_at > now,
        )
        if surface_type:
            stmt = stmt.where(UISurface.surface_type == surface_type)
        stmt = stmt.order_by(UISurface.updated_at.desc()).limit(20)

        result = await db.execute(stmt)
        surfaces = result.scalars().all()

        return UISurfaceListResponse(
            surfaces=[
                UISurfaceResponse(
                    surface_id=s.surface_id,
                    surface_type=s.surface_type,
                    payload=s.payload,
                    created_at=s.created_at.isoformat(),
                    updated_at=s.updated_at.isoformat(),
                )
                for s in surfaces
            ],
            count=len(surfaces),
        )


@router.get("/v1/ui/surfaces/{surface_id}", response_model=UISurfaceResponse)
async def get_surface(
    surface_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """Get a specific A2UI surface by ID."""
    from src.models.database import get_session_factory
    from src.models.ui_state import UISurface

    async with get_session_factory()() as db:
        result = await db.execute(
            select(UISurface).where(
                UISurface.user_id == user_id,
                UISurface.workspace_id == workspace_id,
                UISurface.surface_id == surface_id,
            )
        )
        surface = result.scalar_one_or_none()
        if not surface:
            raise HTTPException(status_code=404, detail="Surface not found")

        return UISurfaceResponse(
            surface_id=surface.surface_id,
            surface_type=surface.surface_type,
            payload=surface.payload,
            created_at=surface.created_at.isoformat(),
            updated_at=surface.updated_at.isoformat(),
        )


class WorkspaceSurfacesResponse(BaseModel):
    surfaces: list[dict]
    count: int


@router.get("/v1/workspace/surfaces", response_model=WorkspaceSurfacesResponse)
async def get_workspace_surfaces(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Unified workspace surfaces — preview + detail_config per surface.

    Returns all surfaces needed by the workspace page in a single call:
    approvals, briefing, priority alerts, recommendations, and persisted
    WS surfaces. Each surface has preview data for grid cards and
    detail_config for modal drill-down tabs.
    """
    from src.services.surface_builder import SurfaceService

    svc = SurfaceService(db, workspace_id)
    surfaces = await svc.build_workspace_surfaces(user_id)

    return WorkspaceSurfacesResponse(
        surfaces=surfaces,
        count=len(surfaces),
    )
