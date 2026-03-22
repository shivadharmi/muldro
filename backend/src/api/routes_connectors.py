"""Connector management routes — backed by ConnectorInstallation."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.config.settings import Settings, get_settings
from src.models.events import NormalizedEvent
from src.services.connector_manager import ConnectorManager

router = APIRouter()
logger = logging.getLogger(__name__)


def _make_connector_manager(db: AsyncSession, settings: Settings) -> ConnectorManager:
    """Create a ConnectorManager wired to OAuthManager for proper token handling."""
    from src.models.database import get_session_factory
    from src.services.oauth_manager import OAuthManager

    db_factory = get_session_factory()
    oauth_mgr = OAuthManager(
        db_factory,
        encryption_key=settings.oauth_encryption_key,
        settings=settings,
    )
    return ConnectorManager(db, oauth_manager=oauth_mgr, settings=settings)


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
    settings: Settings = Depends(get_settings),
):
    """List all connector installations for the current user with event counts."""
    mgr = _make_connector_manager(db, settings)
    connectors = await mgr.get_user_connectors(user_id, workspace_id)

    # Enrich with event counts
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    enriched = []
    for c in connectors:
        provider = c.get("provider", "")
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
        c["events_last_week"] = events_last_week
        c["entities_created"] = 0
        enriched.append(c)

    return {"connectors": enriched}


@router.post("/v1/connectors")
async def create_connector(
    req: ConnectorCreateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Register a new connector."""
    valid_providers = {
        "gmail",
        "calendar",
        "github",
        "slack",
        "drive",
        "web_search",
        "linear",
        "notion",
        "jira",
        "whatsapp",
        "sms",
        "linkedin",
        "twitter",
    }
    if req.provider not in valid_providers:
        raise HTTPException(
            status_code=400, detail=f"Unknown provider. Must be one of: {valid_providers}"
        )
    mgr = _make_connector_manager(db, settings)
    result = await mgr.register_connector(user_id, req.provider, req.config, workspace_id)
    return result


@router.delete("/v1/connectors/{connector_id}")
async def delete_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Disconnect a connector."""
    mgr = _make_connector_manager(db, settings)
    await mgr.disconnect(connector_id, user_id)
    return {"status": "disconnected"}


@router.post("/v1/connectors/{connector_id}/test")
async def test_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Test a connector's connection."""
    mgr = _make_connector_manager(db, settings)
    result = await mgr.test_connector(connector_id, user_id)
    return result


@router.get("/v1/connectors/{connector_id}/insights")
async def get_connector_insights(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get sync health, downstream impact, and dependency analysis for a connector."""
    from src.services.connector_insight import ConnectorInsightService

    # connector_id here is the provider name (e.g., "gmail", "github")
    svc = ConnectorInsightService(db, workspace_id)
    report = await svc.get_insight(connector_id)

    return {
        "provider": report.provider,
        "sync_health": {
            "status": report.sync_health.status,
            "events_last_24h": report.sync_health.events_last_24h,
            "events_last_7d": report.sync_health.events_last_7d,
            "last_event_at": (
                report.sync_health.last_event_at.isoformat()
                if report.sync_health.last_event_at
                else None
            ),
            "avg_events_per_day": report.sync_health.avg_events_per_day,
            "latency_trend": report.sync_health.latency_trend,
        },
        "downstream_impact": {
            "entities_created": report.downstream_impact.entities_created,
            "memories_influenced": report.downstream_impact.memories_influenced,
            "plans_triggered": report.downstream_impact.plans_triggered,
            "briefings_contributed": report.downstream_impact.briefings_contributed,
            "active_webhook_count": report.downstream_impact.active_webhook_count,
        },
        "dependencies": [
            {
                "source_provider": d.source_provider,
                "target_provider": d.target_provider,
                "relationship": d.relationship,
                "description": d.description,
            }
            for d in report.dependencies
        ],
        "recent_events": report.recent_events,
        "recommendations": report.recommendations,
    }


@router.post("/v1/connectors/{connector_id}/poll")
async def poll_connector(
    connector_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Manually trigger a poll for a connector."""
    mgr = _make_connector_manager(db, settings)
    result = await mgr.poll_connector(connector_id, user_id)
    return result
