"""System endpoints — heartbeat, maintenance, diagnostics, metrics, capabilities."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.config.settings import Settings, get_settings
from src.middleware.observability import RequestMetrics
from src.services.dead_letter import DeadLetterService
from src.services.heartbeat import HeartbeatService

router = APIRouter()


class HeartbeatResponse(BaseModel):
    expired_memories: int = 0
    stale_plans_found: int = 0
    plans_escalated: int = 0
    expired_approvals: int = 0
    invalidated_plans: int = 0
    dlq_retried: int = 0
    timestamp: str


class DeadLetterStats(BaseModel):
    total: int = 0
    by_status: dict = {}
    by_operation: dict = {}


@router.post("/v1/system/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Run a heartbeat cycle: expire memories, escalate stale plans, expire approvals."""
    service = HeartbeatService(settings=settings, db=db)
    result = await service.run(user_id)
    await db.commit()
    return HeartbeatResponse(**result)


@router.get("/v1/system/metrics")
async def get_metrics():
    """Return in-memory request metrics."""
    return RequestMetrics.snapshot()


@router.get("/v1/system/dlq", response_model=DeadLetterStats)
async def get_dlq_stats(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Return dead-letter queue statistics."""
    dlq = DeadLetterService(db)
    stats = await dlq.get_stats(user_id)
    return DeadLetterStats(**stats)


@router.get("/v1/system/capabilities")
async def get_capability_health(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get capability health status for the workspace."""
    from src.services.capability_health import CapabilityHealthService

    svc = CapabilityHealthService(db, workspace_id)
    report = await svc.get_health_report()

    return {
        "healthy_count": report.healthy_count,
        "degraded_count": report.degraded_count,
        "unavailable_count": report.unavailable_count,
        "unconfigured_count": report.unconfigured_count,
        "families": [
            {
                "family": f.family,
                "status": f.status,
                "provider": f.provider,
                "last_activity_at": (
                    f.last_activity_at.isoformat() if f.last_activity_at else None
                ),
                "capabilities_available": f.capabilities_available,
                "capabilities_total": f.capabilities_total,
                "message": f.message,
            }
            for f in report.families
        ],
        "last_updated_at": report.last_updated_at.isoformat(),
    }
