"""Trust management API — dashboard, ceiling controls, and time policies."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_current_workspace_id, get_session
from src.models.users import User
from src.services.trust_engine import TrustEngine

router = APIRouter()
logger = logging.getLogger(__name__)

VALID_TRUST_LEVELS = {"first_use", "learning", "trusted", "autonomous", "blocked"}


class TrustRiskLevel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    risk_level: str
    trust_level: str
    approved_count: int
    rejected_count: int
    graduation_progress: dict


class TrustCapabilityEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capability: str
    family: str
    trust_level: str
    ceiling: str
    risk_levels: list[TrustRiskLevel]


class TrustDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capabilities: list[TrustCapabilityEntry]


class TrustCapabilityDetailRisk(BaseModel):
    model_config = ConfigDict(extra="ignore")
    risk_level: str
    trust_level: str
    approved_count: int
    rejected_count: int
    modified_count: int
    last_decision_at: str | None
    cooldown_until: str | None
    graduation_progress: dict


class TrustCapabilityDetailResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capability: str
    family: str
    ceiling: str
    risk_levels: list[TrustCapabilityDetailRisk]


class CeilingRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    max_level: str


class CeilingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capability: str
    max_level: str


class ResetResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capability: str
    status: str


class TimePolicyRule(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start_hour: int
    end_hour: int
    max_level: str
    days: list[int] | None = None


class TimePoliciesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policies: list[TimePolicyRule]


class TimePoliciesRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policies: list[TimePolicyRule]


@router.get("/v1/trust/dashboard", response_model=TrustDashboardResponse)
async def get_trust_dashboard(
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """All capabilities with trust levels, graduation progress, and ceilings."""
    engine = TrustEngine(db, workspace_id=workspace_id)
    entries = await engine.get_trust_dashboard_grouped()
    return TrustDashboardResponse(capabilities=entries)


@router.get(
    "/v1/trust/{capability:path}",
    response_model=TrustCapabilityDetailResponse,
)
async def get_trust_capability(
    capability: str,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Detailed trust state across risk levels for one capability."""
    engine = TrustEngine(db, workspace_id=workspace_id)
    detail = await engine.get_capability_detail(capability)
    return TrustCapabilityDetailResponse(**detail)


@router.put(
    "/v1/trust/{capability:path}/ceiling",
    response_model=CeilingResponse,
)
async def set_trust_ceiling(
    capability: str,
    req: CeilingRequest,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Set the maximum trust level for a capability."""
    if req.max_level not in VALID_TRUST_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid level. Must be one of: {VALID_TRUST_LEVELS}",
        )
    engine = TrustEngine(db, workspace_id=workspace_id)
    await engine.set_ceiling(capability, req.max_level)
    await db.commit()
    return CeilingResponse(capability=capability, max_level=req.max_level)


@router.post(
    "/v1/trust/{capability:path}/reset",
    response_model=ResetResponse,
)
async def reset_trust(
    capability: str,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Reset trust scores for a capability back to first_use."""
    engine = TrustEngine(db, workspace_id=workspace_id)
    await engine.reset_trust_for_capability(capability)
    await db.commit()
    return ResetResponse(capability=capability, status="reset")


@router.get("/v1/trust-time-policies", response_model=TimePoliciesResponse)
async def get_time_policies(
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get time-based trust ceiling overrides."""
    engine = TrustEngine(db, workspace_id=workspace_id)
    policies = await engine.get_time_policies()
    return TimePoliciesResponse(policies=policies)


@router.put("/v1/trust-time-policies", response_model=TimePoliciesResponse)
async def set_time_policies(
    req: TimePoliciesRequest,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Set time-based trust ceiling overrides."""
    for p in req.policies:
        if p.max_level not in VALID_TRUST_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid level '{p.max_level}'. Must be one of: {VALID_TRUST_LEVELS}",
            )
        if not (0 <= p.start_hour <= 23 and 0 <= p.end_hour <= 23):
            raise HTTPException(status_code=400, detail="Hours must be 0-23")
    engine = TrustEngine(db, workspace_id=workspace_id)
    await engine.set_time_policies([p.model_dump() for p in req.policies])
    await db.commit()
    return TimePoliciesResponse(policies=req.policies)
