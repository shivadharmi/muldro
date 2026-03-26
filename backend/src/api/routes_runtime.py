"""API routes for runtime projections.

Endpoints:
  GET /v1/runtime/summary   — aggregate runtime summary
  GET /v1/runtime/activity  — recent runtime events
  GET /v1/runtime/runs      — active runs with progress
  GET /v1/runtime/blocked   — blocked/awaiting runs
  GET /v1/runtime/agents    — agent workload distribution
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_workspace_id
from src.api.schemas.runtime import (
    AgentWorkloadResponse,
    RuntimeEventResponse,
    RuntimeRunResponse,
    RuntimeSummaryResponse,
)
from src.models.database import get_db

router = APIRouter(prefix="/v1/runtime")


@router.get("/summary", response_model=RuntimeSummaryResponse)
async def get_runtime_summary(
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.services.runtime_projection import RuntimeProjectionService

    svc = RuntimeProjectionService(db, workspace_id)
    summary = await svc.get_runtime_summary()
    return RuntimeSummaryResponse(
        active_runs=summary["active_runs"],
        blocked_runs=summary["blocked_runs"],
        completed_24h=summary["completed_24h"],
        failed_24h=summary["failed_24h"],
        agents_active=summary["agents_active"],
        top_agents=[AgentWorkloadResponse(**a) for a in summary["top_agents"]],
    )


@router.get("/activity", response_model=list[RuntimeEventResponse])
async def get_runtime_activity(
    event_type: str | None = None,
    limit: int = Query(default=50, le=200),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.services.runtime_projection import RuntimeProjectionService

    svc = RuntimeProjectionService(db, workspace_id)
    event_types = [event_type] if event_type else None
    events = await svc.get_recent_events(event_types=event_types, limit=limit)
    return [RuntimeEventResponse(**e) for e in events]


@router.get("/runs", response_model=list[RuntimeRunResponse])
async def get_active_runs(
    limit: int = Query(default=20, le=100),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.services.runtime_projection import RuntimeProjectionService

    svc = RuntimeProjectionService(db, workspace_id)
    runs = await svc.get_active_runs(limit=limit)
    return [RuntimeRunResponse(**r) for r in runs]


@router.get("/blocked", response_model=list[dict])
async def get_blocked_runs(
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.services.runtime_projection import RuntimeProjectionService

    svc = RuntimeProjectionService(db, workspace_id)
    return await svc.get_blocked_runs()


@router.get("/agents", response_model=list[AgentWorkloadResponse])
async def get_agent_workload(
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.services.runtime_projection import RuntimeProjectionService

    svc = RuntimeProjectionService(db, workspace_id)
    workloads = await svc.get_agent_workload()
    return [AgentWorkloadResponse(**w) for w in workloads]


@router.get("/evidence/{run_id}")
async def get_run_evidence(
    run_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.services.evidence_bundle import EvidenceBundleService

    svc = EvidenceBundleService(db, workspace_id)
    evidence = await svc.build_for_run(run_id)
    return evidence
