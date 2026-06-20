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
    # Unified run surface
    "run_": ("run", "run_id"),
    "summary_": ("summary", "run_id"),
    # System surfaces
    "approval_": ("approval", "approval_id"),
    "briefing_": ("briefing", "briefing_id"),
    "priority_": ("alert", "run_id"),
    "rec_": ("recommendation", "index"),
    # Legacy
    "exec_": ("plan", "run_id"),
    "surf_": ("_from_db", "surface_id"),
}


def _resolve_ephemeral(surface_id: str) -> tuple[str, dict] | None:
    """Resolve kind and metadata from an ephemeral surface_id prefix."""
    for prefix, (kind, ref_key) in _PREFIX_MAP.items():
        if surface_id.startswith(prefix):
            if kind == "_from_db":
                return None  # force DB lookup path
            ref_value = surface_id[len(prefix) :]
            return kind, {ref_key: ref_value, "surface_id": surface_id}
    return None


async def _verify_ephemeral_ownership(db: AsyncSession, metadata: dict, user_id: str) -> None:
    """Raise 404 if the record an ephemeral surface_id references exists but is
    owned by a different user. Missing records are allowed through so the builder
    can render its own empty-state. Only id-bearing references are checked."""
    checks: list[tuple[str, type, str]] = []
    if metadata.get("run_id"):
        from src.models.task_graph import TaskRun

        checks.append((metadata["run_id"], TaskRun, "run_id"))
    if metadata.get("approval_id"):
        from src.models.approvals import Approval

        checks.append((metadata["approval_id"], Approval, "approval_id"))
    if metadata.get("briefing_id"):
        from src.models.briefings import Briefing

        checks.append((metadata["briefing_id"], Briefing, "briefing_id"))

    for ref_value, model, id_attr in checks:
        row = (
            await db.execute(select(model).where(getattr(model, id_attr) == ref_value))
        ).scalar_one_or_none()
        if row is not None and getattr(row, "user_id", None) != user_id:
            # Do not distinguish "not yours" from "not found" to avoid id enumeration.
            raise HTTPException(status_code=404, detail="Surface not found.")


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
        # Tenant guard: ephemeral surfaces reference a workspace-scoped record by id
        # embedded in the surface_id. Unlike the persisted path (filtered by user_id),
        # nothing here verifies the caller owns that record, so a guessed/enumerated id
        # could read another tenant's run/approval/briefing detail. Verify ownership
        # when the referenced record exists; a genuinely-missing record falls through
        # to the builder's own empty-state (preserving "No linked …" UX).
        await _verify_ephemeral_ownership(db, metadata, user_id)
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
