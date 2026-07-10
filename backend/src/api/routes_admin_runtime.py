"""Admin runtime kill-switch — the manual escape hatch (Step 10B Phase 5 Task 5b).

The Phase 5 auto-rollback watcher (``src/services/scheduler/runtime_rollback_tick.py``)
is ONE-DIRECTIONAL: it can only trip a regressing surface to ``"legacy"``, never flip one
back to ``"deep"``, and never touches a surface a human has already intervened on. This
route is that human intervention path — a blunt, always-wins override for genuine
emergencies (or the deliberate steps of the 10D cutover runbook), sitting above every
other resolution tier in ``effective_runtime`` (``src/services/runtime_gate.py``):
override > breaker > enabled > static settings.

Routine rollout control is the separate enable-key path (10D); this route is the
escape hatch, not the dial.

# TODO(10D): retire escape hatch after each surface clears its clean week
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.api.deps import get_current_user
from src.models.users import User
from src.services import runtime_breaker

router = APIRouter(prefix="/v1/admin/runtime")
logger = logging.getLogger(__name__)

_VALID_TARGETS = ("legacy", "deep")
_VALID_OVERRIDE_SURFACES = (*runtime_breaker.VALID_SURFACES, "all")


class RuntimeOverrideRequest(BaseModel):
    surface: str
    target: str = "legacy"


class RuntimeOverrideResponse(BaseModel):
    surface: str
    target: str
    status: str


def _redis_or_503(request: Request):
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable for runtime override")
    return redis


@router.post("/override", response_model=RuntimeOverrideResponse)
async def set_runtime_override(
    req: RuntimeOverrideRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Force ``req.surface`` (or ``"all"``) to ``req.target`` via the manual override
    key — wins over every other resolution tier, including a tripped breaker."""
    if req.surface not in _VALID_OVERRIDE_SURFACES:
        raise HTTPException(status_code=400, detail=f"Invalid surface: {req.surface!r}")
    if req.target not in _VALID_TARGETS:
        raise HTTPException(status_code=400, detail=f"Invalid target: {req.target!r}")

    redis = _redis_or_503(request)
    await runtime_breaker.set_manual_override(redis, req.surface, target=req.target)
    logger.warning(
        "admin runtime override SET by user=%s: surface=%s target=%s",
        user.user_id,
        req.surface,
        req.target,
    )
    return RuntimeOverrideResponse(surface=req.surface, target=req.target, status="set")


@router.delete("/override/{surface}", response_model=RuntimeOverrideResponse)
async def clear_runtime_override(
    surface: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Clear the manual override for ``surface`` (or ``"all"``), restoring resolution
    to the breaker/enabled/static tiers."""
    if surface not in _VALID_OVERRIDE_SURFACES:
        raise HTTPException(status_code=400, detail=f"Invalid surface: {surface!r}")

    redis = _redis_or_503(request)
    await runtime_breaker.clear_manual_override(redis, surface)
    logger.warning("admin runtime override CLEARED by user=%s: surface=%s", user.user_id, surface)
    return RuntimeOverrideResponse(surface=surface, target="", status="cleared")
