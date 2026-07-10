"""Admin runtime kill-switch — the manual escape hatch (Step 10B Phase 5 Task 5b).

The Phase 5 auto-rollback watcher (``src/services/scheduler/runtime_rollback_tick.py``)
is ONE-DIRECTIONAL: it can only trip a regressing surface to ``"legacy"``, never flip one
back to ``"deep"``, and never touches a surface a human has already intervened on. This
route is that human intervention path — a blunt, always-wins override for genuine
emergencies (or the deliberate steps of the 10D cutover runbook), sitting above every
other resolution tier in ``effective_runtime`` (``src/services/runtime_gate.py``):
override > breaker > enabled > static settings.

Two safety properties (both hardened after the Phase-5 security review):

* SAFE-DIRECTION ONLY. The override may force a surface to ``"legacy"`` only
  (``_VALID_TARGETS = ("legacy",)``). Flipping a surface to ``"deep"`` is the SEPARATE
  controlled ENABLE-key rollout path (10D), NOT this escape hatch — otherwise any caller
  could force ``"deep"`` (the override tier outranks even a tripped breaker) and defeat
  the auto-rollback watcher. A ``target="deep"`` request is rejected 400.
* OPERATOR-ONLY, FAIL-CLOSED. The whole router is gated by ``require_admin``: a request
  must carry an ``X-Admin-Token`` header matching ``settings.admin_api_token`` (compared
  in constant time). With the token unset (the default), the route is DISABLED and every
  request is rejected 403 — an unprivileged user can never reach the override keyspace.

Routine rollout control is the separate enable-key path (10D); this route is the
escape hatch, not the dial.

# TODO(10D): retire escape hatch after each surface clears its clean week
"""

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from src.config.settings import Settings, get_settings
from src.services import runtime_breaker

logger = logging.getLogger(__name__)

# Escape hatch forces the SAFE direction only. "deep" is the separate ENABLE-key path.
_VALID_TARGETS = ("legacy",)
_VALID_OVERRIDE_SURFACES = (*runtime_breaker.VALID_SURFACES, "all")


async def require_admin(
    x_admin_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Operator-only gate for the runtime kill-switch — FAIL-CLOSED.

    Rejects 403 when the admin token is unset server-side (route disabled by default),
    when the ``X-Admin-Token`` header is missing, or when it does not match. The compare
    is constant-time (``hmac.compare_digest``) to avoid leaking the token via timing.
    """
    expected = settings.admin_api_token
    if not expected:
        # Fail-closed: with no configured token the admin route is disabled entirely.
        raise HTTPException(status_code=403, detail="Admin runtime API is disabled")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")


# The admin token is the operator credential — no per-user session is required (an ops
# caller authenticates with the token, not a user login). require_admin is the gate.
router = APIRouter(prefix="/v1/admin/runtime", dependencies=[Depends(require_admin)])


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
):
    """Force ``req.surface`` (or ``"all"``) to ``"legacy"`` via the manual override key —
    wins over every other resolution tier, including a tripped breaker. Only the SAFE
    direction is permitted; a ``target="deep"`` request is rejected 400."""
    if req.surface not in _VALID_OVERRIDE_SURFACES:
        raise HTTPException(status_code=400, detail=f"Invalid surface: {req.surface!r}")
    if req.target not in _VALID_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid target: {req.target!r}. The escape hatch forces the SAFE "
                "direction only (legacy); enabling 'deep' is the separate rollout path."
            ),
        )

    redis = _redis_or_503(request)
    await runtime_breaker.set_manual_override(redis, req.surface, target=req.target)
    logger.warning(
        "admin runtime override SET: surface=%s target=%s",
        req.surface,
        req.target,
    )
    return RuntimeOverrideResponse(surface=req.surface, target=req.target, status="set")


@router.delete("/override/{surface}", response_model=RuntimeOverrideResponse)
async def clear_runtime_override(
    surface: str,
    request: Request,
):
    """Clear the manual override for ``surface`` (or ``"all"``), restoring resolution
    to the breaker/enabled/static tiers."""
    if surface not in _VALID_OVERRIDE_SURFACES:
        raise HTTPException(status_code=400, detail=f"Invalid surface: {surface!r}")

    redis = _redis_or_503(request)
    await runtime_breaker.clear_manual_override(redis, surface)
    logger.warning("admin runtime override CLEARED: surface=%s", surface)
    return RuntimeOverrideResponse(surface=surface, target="", status="cleared")
