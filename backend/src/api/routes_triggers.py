"""Trigger CRUD routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.triggers import Trigger

router = APIRouter()
logger = logging.getLogger(__name__)


class TriggerItem(BaseModel):
    trigger_id: str
    name: str
    description: str | None = None
    conditions: dict
    action_type: str
    action_config: dict | None = None
    enabled: bool = True
    fire_count: int = 0
    last_fired_at: str | None = None
    created_at: str | None = None

    model_config = {"from_attributes": True}


class TriggerListResponse(BaseModel):
    triggers: list[TriggerItem]


class TriggerCreateRequest(BaseModel):
    name: str
    description: str | None = None
    conditions: dict
    action_type: str
    action_config: dict | None = None
    enabled: bool = True


class TriggerPatchRequest(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    conditions: dict | None = None
    action_type: str | None = None
    action_config: dict | None = None


def _to_item(t: Trigger) -> TriggerItem:
    return TriggerItem(
        trigger_id=t.trigger_id,
        name=t.name,
        description=t.description,
        conditions=t.conditions,
        action_type=t.action_type,
        action_config=t.action_config,
        enabled=t.enabled,
        fire_count=t.fire_count,
        last_fired_at=t.last_fired_at.isoformat() if t.last_fired_at else None,
        created_at=t.created_at.isoformat() if t.created_at else None,
    )


@router.get("/v1/triggers", response_model=TriggerListResponse)
async def list_triggers(
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List triggers for the current user."""
    stmt = (
        select(Trigger)
        .where(Trigger.user_id == user_id, Trigger.workspace_id == workspace_id)
        .order_by(Trigger.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return TriggerListResponse(triggers=[_to_item(t) for t in rows])


@router.post("/v1/triggers", response_model=TriggerItem, status_code=201)
async def create_trigger(
    req: TriggerCreateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Create a new trigger."""
    trigger = Trigger(
        trigger_id=f"trg_{ULID()}",
        user_id=user_id,
        workspace_id=workspace_id,
        name=req.name,
        description=req.description,
        conditions=req.conditions,
        action_type=req.action_type,
        action_config=req.action_config,
        enabled=req.enabled,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    return _to_item(trigger)


@router.patch("/v1/triggers/{trigger_id}", response_model=TriggerItem)
async def patch_trigger(
    trigger_id: str,
    req: TriggerPatchRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Update a trigger (partial)."""
    result = await db.execute(
        select(Trigger).where(
            Trigger.trigger_id == trigger_id,
            Trigger.user_id == user_id,
            Trigger.workspace_id == workspace_id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    for field, value in req.model_dump(exclude_none=True).items():
        setattr(trigger, field, value)

    await db.commit()
    await db.refresh(trigger)
    return _to_item(trigger)


@router.delete("/v1/triggers/{trigger_id}", status_code=204)
async def delete_trigger(
    trigger_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Delete a trigger."""
    result = await db.execute(
        select(Trigger).where(
            Trigger.trigger_id == trigger_id,
            Trigger.user_id == user_id,
            Trigger.workspace_id == workspace_id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    await db.delete(trigger)
    await db.commit()
