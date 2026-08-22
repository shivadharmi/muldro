"""System endpoints — heartbeat, maintenance, diagnostics, metrics, capabilities."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.config.settings import Settings, get_settings
from src.middleware.observability import RequestMetrics
from src.services.dead_letter import DeadLetterService
from src.services.heartbeat import HeartbeatService

logger = logging.getLogger(__name__)
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


class AuditLogEntry(BaseModel):
    audit_id: str
    user_id: str
    action_type: str
    summary: str | None = None
    policy_decision: str | None = None
    event_id: str | None = None
    plan_id: str | None = None
    execution_id: str | None = None
    approval_id: str | None = None
    details: dict | None = None
    created_at: str | None = None


@router.get("/v1/system/audit", response_model=list[AuditLogEntry])
async def list_audit_logs(
    action_type: str | None = None,
    limit: int = 50,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List audit log entries for the workspace."""
    from sqlalchemy import select

    from src.models.audit import AuditLog

    stmt = select(AuditLog).where(AuditLog.workspace_id == workspace_id)
    if action_type:
        stmt = stmt.where(AuditLog.action_type == action_type)
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(min(limit, 200))

    result = await db.execute(stmt)
    entries = result.scalars().all()

    return [
        AuditLogEntry(
            audit_id=e.audit_id,
            user_id=e.user_id,
            action_type=e.action_type,
            summary=e.summary,
            policy_decision=e.policy_decision,
            event_id=e.event_id,
            plan_id=e.plan_id,
            execution_id=e.execution_id,
            approval_id=e.approval_id,
            details=e.details,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in entries
    ]


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
