"""Webhook endpoints — backwards-compatible generic webhook passthrough.

Source-specific webhook endpoints have been removed. All event ingestion
now goes through /v1/events/ingest (routes_events.py). This file keeps
a single /v1/webhooks/generic endpoint for backwards compatibility.
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.routes_events import _make_event_processor
from src.api.schemas import EventIngestResponse
from src.config.settings import Settings, get_settings
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/webhooks/generic", response_model=EventIngestResponse)
async def generic_webhook(
    request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Receive generic webhook payload. Forwards to event ingestion pipeline."""
    body = await request.json()

    raw = RawEvent(
        source=body.get("source", "webhook"),
        source_account_id=body.get("source", "webhook") + "_default",
        event_type=body.get("event_type", "generic"),
        entity_type=body.get("entity_type", "unknown"),
        entity_id=body.get("entity_id", "unknown"),
        title=body.get("title"),
        summary=body.get("summary"),
        actor=body.get("actor"),
        occurred_at=None,
        raw_payload=body,
    )

    processor = _make_event_processor(settings, db)
    event_id = await processor.process(raw, user_id)

    if event_id is None:
        return EventIngestResponse(event_id=None, status="duplicate", importance_score=None)

    return EventIngestResponse(event_id=event_id, status="processed", importance_score=None)
