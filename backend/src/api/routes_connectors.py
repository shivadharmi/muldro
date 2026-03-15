"""Connector management routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
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
    db: AsyncSession = Depends(get_session),
):
    """List all connectors for the current user."""
    mgr = ConnectorManager(db)
    connectors = await mgr.get_user_connectors(user_id)
    return {"connectors": connectors}


@router.post("/v1/connectors")
async def create_connector(
    req: ConnectorCreateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Register a new connector."""
    valid_providers = {"gmail", "calendar", "github", "slack"}
    if req.provider not in valid_providers:
        raise HTTPException(
            status_code=400, detail=f"Unknown provider. Must be one of: {valid_providers}"
        )
    mgr = ConnectorManager(db)
    result = await mgr.register_connector(user_id, req.provider, req.config)
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
