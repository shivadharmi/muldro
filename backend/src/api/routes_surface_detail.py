"""Detail tab endpoint for surface modal drill-down.

GET /v1/surfaces/{surface_id}/detail/{tab_id}

Resolves the surface from ui_surfaces (persisted WS surfaces) OR from the
surface_id prefix (ephemeral surfaces built by SurfaceService). Dispatches
to the appropriate tab builder based on (kind, tab_id).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.models.ui_state import UISurface
from src.services.surface_detail_builders import TAB_BUILDERS
from src.ui.contracts import DetailTabResponse

router = APIRouter()

# Ephemeral surface ID prefixes → (kind, reference_key)
_PREFIX_MAP: dict[str, tuple[str, str]] = {
    "approval_": ("approval", "approval_id"),
    "briefing_": ("briefing", "briefing_id"),
    "priority_": ("alert", "run_id"),
    "rec_": ("recommendation", "index"),
    "exec_": ("plan", "run_id"),
}


def _resolve_ephemeral(surface_id: str) -> tuple[str, dict] | None:
    """Resolve kind and metadata from an ephemeral surface_id prefix."""
    for prefix, (kind, ref_key) in _PREFIX_MAP.items():
        if surface_id.startswith(prefix):
            ref_value = surface_id[len(prefix) :]
            return kind, {ref_key: ref_value, "surface_id": surface_id}
    return None


class _VirtualSurface:
    """Lightweight stand-in for UISurface when no DB row exists."""

    def __init__(self, surface_id: str, surface_type: str, payload: dict):
        self.surface_id = surface_id
        self.surface_type = surface_type
        self.payload = payload


@router.get(
    "/v1/surfaces/{surface_id}/detail/{tab_id}",
    response_model=DetailTabResponse,
)
async def get_surface_detail(
    surface_id: str,
    tab_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Fetch detail tab content for a surface modal."""
    # Try persisted surface first (WS-pushed surfaces)
    result = await db.execute(
        select(UISurface).where(
            UISurface.surface_id == surface_id,
            UISurface.user_id == user_id,
        )
    )
    surface = result.scalar_one_or_none()

    if surface:
        kind = surface.surface_type
    else:
        # Ephemeral surface — resolve kind from ID prefix
        resolved = _resolve_ephemeral(surface_id)
        if not resolved:
            raise HTTPException(status_code=404, detail="Surface not found.")
        kind, metadata = resolved
        surface = _VirtualSurface(
            surface_id=surface_id,
            surface_type=kind,
            payload={"metadata": metadata},
        )

    builder = TAB_BUILDERS.get((kind, tab_id))
    if not builder:
        raise HTTPException(
            status_code=404,
            detail=f"No tab '{tab_id}' for surface kind '{kind}'.",
        )

    return await builder(db, surface)
