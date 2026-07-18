"""Per-workspace chat settings — the default ``permission_mode`` (auto/ask/bypass).

Stored in ``Workspace.settings["default_permission_mode"]`` (JSONB; NO migration). Workspace-scoped
via ``get_current_workspace_id`` (mirrors routes_trust). The default is the fallback the interactive
chat handler applies when a request omits ``permission_mode``; it is never read on the
pinned-caller / autonomous paths.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_current_workspace_id, get_session
from src.models.database import get_session_factory
from src.models.users import User, Workspace
from src.services.workspace_entitlements import (
    VALID_PERMISSION_MODES,
    workspace_default_permission_mode,
)

router = APIRouter()


class DefaultPermissionModeResponse(BaseModel):
    default_permission_mode: str


class DefaultPermissionModeRequest(BaseModel):
    default_permission_mode: Literal["auto", "ask", "bypass"]


def _merged_settings(current: dict | None, value: str) -> dict:
    """Return a NEW settings dict with the default set, preserving all sibling keys."""
    return {**(current or {}), "default_permission_mode": value}


@router.get("/v1/workspace/permission-mode-default", response_model=DefaultPermissionModeResponse)
async def get_default_permission_mode(
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """The workspace's default chat permission mode (fail-safe ``auto``)."""
    value = await workspace_default_permission_mode(get_session_factory(), workspace_id)
    return DefaultPermissionModeResponse(default_permission_mode=value)


@router.put("/v1/workspace/permission-mode-default", response_model=DefaultPermissionModeResponse)
async def set_default_permission_mode(
    req: DefaultPermissionModeRequest,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Set the workspace default (JSONB merge; preserves allow_bypass + other keys)."""
    if req.default_permission_mode not in VALID_PERMISSION_MODES:
        raise HTTPException(status_code=400, detail="Invalid permission mode")
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    # Reassign a NEW dict (not in-place mutate) so SQLAlchemy detects the JSONB change.
    workspace.settings = _merged_settings(workspace.settings, req.default_permission_mode)
    await db.commit()
    return DefaultPermissionModeResponse(default_permission_mode=req.default_permission_mode)
