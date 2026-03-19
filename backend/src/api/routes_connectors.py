"""Connector management routes."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.events import NormalizedEvent
from src.services.connector_manager import ConnectorManager

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectorCreateRequest(BaseModel):
    provider: str
    config: dict | None = None


class ConnectorSettingsRequest(BaseModel):
    config: dict


@router.get("/v1/connectors")
async def list_connectors(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List all connectors for the current user with event counts."""
    mgr = ConnectorManager(db)
    connectors = await mgr.get_user_connectors(user_id)

    # Enrich with event counts
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    enriched = []
    for c in connectors:
        provider = c.get("provider", "") if isinstance(c, dict) else getattr(c, "provider", "")
        events_last_week = (
            await db.scalar(
                select(func.count())
                .select_from(NormalizedEvent)
                .where(
                    NormalizedEvent.workspace_id == workspace_id,
                    NormalizedEvent.source == provider,
                    NormalizedEvent.occurred_at > week_ago,
                )
            )
            or 0
        )
        if isinstance(c, dict):
            c["events_last_week"] = events_last_week
            c["entities_created"] = 0
            enriched.append(c)
        else:
            enriched.append(
                {
                    **c.__dict__,
                    "events_last_week": events_last_week,
                    "entities_created": 0,
                }
            )

    return {"connectors": enriched}


@router.post("/v1/connectors")
async def create_connector(
    req: ConnectorCreateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Register a new connector."""
    valid_providers = {"gmail", "calendar", "github", "slack"}
    if req.provider not in valid_providers:
        raise HTTPException(
            status_code=400, detail=f"Unknown provider. Must be one of: {valid_providers}"
        )
    mgr = ConnectorManager(db)
    result = await mgr.register_connector(user_id, req.provider, req.config, workspace_id)
    return result


@router.delete("/v1/connectors/{connector_id}")
async def delete_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Disconnect a connector."""
    mgr = ConnectorManager(db)
    await mgr.disconnect(connector_id, user_id)
    return {"status": "disconnected"}


@router.post("/v1/connectors/{connector_id}/test")
async def test_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Test a connector's connection."""
    mgr = ConnectorManager(db)
    result = await mgr.test_connector(connector_id, user_id)
    return result


@router.post("/v1/connectors/{connector_id}/poll")
async def poll_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Manually trigger a poll for a connector."""
    mgr = ConnectorManager(db)
    result = await mgr.poll_connector(connector_id, user_id)
    return result
