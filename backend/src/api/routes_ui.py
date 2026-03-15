"""REST endpoints for fetching A2UI surface state.

Provides endpoints for the frontend to fetch the latest surface payloads
when WebSocket is not available or on initial page load.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

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


@router.get("/v1/ui/surfaces/{user_id}", response_model=UISurfaceListResponse)
async def get_user_surfaces(user_id: str, surface_type: str = ""):
    """Get latest A2UI surfaces for a user.

    Optionally filter by surface_type (briefing, approval, dashboard).
    """
    from src.models.database import get_session_factory
    from src.models.ui_state import UISurface

    async with get_session_factory()() as db:
        stmt = select(UISurface).where(UISurface.user_id == user_id)
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


@router.get("/v1/ui/surfaces/{user_id}/{surface_id}", response_model=UISurfaceResponse)
async def get_surface(user_id: str, surface_id: str):
    """Get a specific A2UI surface by ID."""
    from src.models.database import get_session_factory
    from src.models.ui_state import UISurface

    async with get_session_factory()() as db:
        result = await db.execute(
            select(UISurface).where(
                UISurface.user_id == user_id,
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
