"""Webhook endpoints — receive external service pushes (Gmail, Calendar, Slack)."""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.schemas import GmailTestPayload, WebhookResponse
from src.config.settings import Settings, get_settings
from src.connectors.gmail import GmailConnector
from src.services.event_processor import EventProcessor

logger = logging.getLogger(__name__)

router = APIRouter()


def _make_gmail_connector(settings: Settings, db: AsyncSession) -> GmailConnector:
    event_processor = EventProcessor(settings=settings, db=db)
    return GmailConnector(settings=settings, db=db, event_processor=event_processor)


@router.post("/v1/webhooks/gmail", response_model=WebhookResponse)
async def gmail_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Receive Gmail push notification (from Google Pub/Sub or test payload).

    Accepts a JSON body with a "messages" array of GmailMessagePayload objects.
    """
    body = await request.json()
    connector = _make_gmail_connector(settings, db)
    # Use a default user_id; in production this would be resolved from the push subscription
    user_id = body.get("user_id", "usr_default")
    event_ids = await connector.handle_push_notification(body, user_id)
    return WebhookResponse(received=True, event_id=event_ids[0] if event_ids else None)


@router.post("/v1/webhooks/gmail/test", response_model=WebhookResponse)
async def gmail_test_webhook(
    payload: GmailTestPayload,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Test endpoint — accepts a single GmailMessagePayload directly (no Pub/Sub envelope)."""
    from src.connectors.gmail import GmailMessagePayload

    msg = GmailMessagePayload.model_validate(payload.model_dump())
    connector = _make_gmail_connector(settings, db)
    event_id = await connector.process_test_message(msg, user_id)
    return WebhookResponse(received=True, event_id=event_id)


@router.post("/v1/webhooks/calendar", response_model=WebhookResponse)
async def calendar_webhook(request: Request):
    """Receive Calendar change notification."""
    _body = await request.json()  # noqa: F841
    # TODO: Wire to Calendar connector service (Sprint 4)
    return WebhookResponse(received=True)


@router.post("/v1/webhooks/slack", response_model=WebhookResponse)
async def slack_webhook(request: Request):
    """Receive Slack event notification."""
    _body = await request.json()  # noqa: F841
    # TODO: Wire to Slack connector service
    return WebhookResponse(received=True)


@router.post("/v1/webhooks/generic", response_model=WebhookResponse)
async def generic_webhook(request: Request):
    """Receive generic connector webhook."""
    _body = await request.json()  # noqa: F841
    # TODO: Route to appropriate connector
    return WebhookResponse(received=True)
