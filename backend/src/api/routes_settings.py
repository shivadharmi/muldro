"""User settings routes — per-user configuration management."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.models.users import User
from src.services.settings_service import SettingsService

router = APIRouter()
logger = logging.getLogger(__name__)


class SettingUpdateRequest(BaseModel):
    value: object


class PolicyModeRequest(BaseModel):
    mode: str  # lockdown, approval_required, suggest_only, full_auto


class BudgetLimitRequest(BaseModel):
    daily_limit_usd: float


class ObservationIntervalRequest(BaseModel):
    interval_minutes: int


class SettingsResponse(BaseModel):
    settings: dict


class PolicyResponse(BaseModel):
    mode: str


class BudgetResponse(BaseModel):
    daily_limit_usd: float


# ── Settings CRUD ────────────────────────────────────────────


@router.get("/v1/settings", response_model=SettingsResponse)
async def get_all_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get all user settings grouped by category."""
    svc = SettingsService(db)
    settings = await svc.get_all(user.user_id)
    return SettingsResponse(settings=settings)


@router.put("/v1/settings/{category}/{key}")
async def update_setting(
    category: str,
    key: str,
    req: SettingUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Update a single setting."""
    svc = SettingsService(db)
    await svc.set(user.user_id, category, key, req.value)
    await db.commit()
    return {"status": "updated", "category": category, "key": key}


# ── Policy ───────────────────────────────────────────────────


@router.get("/v1/settings/policy", response_model=PolicyResponse)
async def get_policy_mode(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get current policy mode."""
    svc = SettingsService(db)
    mode = await svc.get_policy_mode(user.user_id)
    return PolicyResponse(mode=mode)


@router.put("/v1/settings/policy/mode", response_model=PolicyResponse)
async def set_policy_mode(
    req: PolicyModeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Change the global policy mode."""
    valid_modes = {"lockdown", "approval_required", "suggest_only", "full_auto"}
    if req.mode not in valid_modes:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of: {valid_modes}")

    svc = SettingsService(db)
    await svc.set(user.user_id, "policy", "mode", req.mode)
    await db.commit()
    return PolicyResponse(mode=req.mode)


# ── Budget ───────────────────────────────────────────────────


@router.get("/v1/settings/budget", response_model=BudgetResponse)
async def get_budget(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get daily budget limit."""
    svc = SettingsService(db)
    limit = await svc.get_budget_limit(user.user_id)
    return BudgetResponse(daily_limit_usd=limit)


@router.put("/v1/settings/budget/daily_limit", response_model=BudgetResponse)
async def set_budget_limit(
    req: BudgetLimitRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Change the daily budget limit."""
    svc = SettingsService(db)
    await svc.set(user.user_id, "budget", "daily_limit_usd", req.daily_limit_usd)
    await db.commit()
    return BudgetResponse(daily_limit_usd=req.daily_limit_usd)


# ── Connectors / Observation ─────────────────────────────────


@router.get("/v1/settings/connectors")
async def get_connector_intervals(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get observation polling intervals for each connector."""
    svc = SettingsService(db)
    intervals = await svc.get_observation_intervals(user.user_id)
    return {"intervals": intervals}


@router.put("/v1/settings/connectors/{source}/interval")
async def set_connector_interval(
    source: str,
    req: ObservationIntervalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Set the observation polling interval for a connector."""
    svc = SettingsService(db)
    key = f"{source}_interval_minutes"
    await svc.set(user.user_id, "observation", key, req.interval_minutes)
    await db.commit()
    return {"status": "updated", "source": source, "interval_minutes": req.interval_minutes}
