"""Webhook endpoints — receive external service pushes (Gmail, Calendar, Slack)."""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.schemas import (
    CalendarTestPayload,
    GmailTestPayload,
    SlackTestPayload,
    WebhookResponse,
    WhatsAppTestPayload,
)
from src.config.settings import Settings, get_settings
from src.connectors.calendar import CalendarConnector
from src.connectors.gmail import GmailConnector
from src.connectors.slack import SlackConnector
from src.connectors.whatsapp import WhatsAppConnector
from src.services.event_processor import EventProcessor
from src.services.memory_service import MemoryService
from src.services.planner import Planner
from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)

router = APIRouter()


def _make_event_processor(settings: Settings, db: AsyncSession) -> EventProcessor:
    """Build an EventProcessor with the full callback pipeline.

    Callbacks (in order):
    1. Entity extraction (WorldModel)
    2. Memory extraction (MemoryService)
    3. Proactive planning (Planner — for high-importance events)
    """
    world_model = WorldModel(settings=settings, db=db)
    memory_service = MemoryService(settings=settings, db=db)
    planner = Planner(
        settings=settings,
        db=db,
        world_model=world_model,
        memory_service=memory_service,
    )

    async def _extract_entities(event_id: str, user_id: str) -> None:
        await world_model.extract_from_event(event_id, user_id)

    async def _extract_memories(event_id: str, user_id: str) -> None:
        from sqlalchemy import select as sa_select

        from src.models.events import NormalizedEvent

        result = await db.execute(
            sa_select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()
        if event and event.summary:
            await memory_service.extract_and_store(user_id, event.summary, [event_id])

    async def _proactive_plan(event_id: str, user_id: str) -> None:
        """Auto-trigger planning for high-importance events."""
        plan = await planner.plan_for_event(event_id, user_id)
        if plan:
            logger.info(
                "Proactive plan created: %s decision=%s for event %s",
                plan.plan_id,
                plan.decision,
                event_id,
            )

    return EventProcessor(
        settings=settings,
        db=db,
        on_event_processed=[_extract_entities, _extract_memories, _proactive_plan],
        world_model=world_model,
        memory_service=memory_service,
    )


def _make_gmail_connector(settings: Settings, db: AsyncSession) -> GmailConnector:
    return GmailConnector(
        settings=settings, db=db, event_processor=_make_event_processor(settings, db)
    )


def _make_calendar_connector(settings: Settings, db: AsyncSession) -> CalendarConnector:
    return CalendarConnector(
        settings=settings, db=db, event_processor=_make_event_processor(settings, db)
    )


def _make_slack_connector(settings: Settings, db: AsyncSession) -> SlackConnector:
    return SlackConnector(
        settings=settings, db=db, event_processor=_make_event_processor(settings, db)
    )


def _make_whatsapp_connector(settings: Settings, db: AsyncSession) -> WhatsAppConnector:
    return WhatsAppConnector(
        settings=settings, db=db, event_processor=_make_event_processor(settings, db)
    )


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
async def calendar_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Receive Calendar change notification."""
    body = await request.json()
    connector = _make_calendar_connector(settings, db)
    user_id = body.get("user_id", "usr_default")
    event_ids = await connector.handle_push_notification(body, user_id)
    return WebhookResponse(received=True, event_id=event_ids[0] if event_ids else None)


@router.post("/v1/webhooks/calendar/test", response_model=WebhookResponse)
async def calendar_test_webhook(
    payload: CalendarTestPayload,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Test endpoint — accepts a single CalendarEventPayload directly."""
    from src.connectors.calendar import CalendarEventPayload

    evt = CalendarEventPayload.model_validate(payload.model_dump())
    connector = _make_calendar_connector(settings, db)
    event_id = await connector.process_test_event(evt, user_id)
    return WebhookResponse(received=True, event_id=event_id)


@router.post("/v1/webhooks/slack", response_model=WebhookResponse)
async def slack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Receive Slack Events API callback.

    Handles url_verification challenge and event_callback types.
    """
    body = await request.json()

    # Handle Slack URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    if body.get("type") != "event_callback":
        return WebhookResponse(received=True)

    connector = _make_slack_connector(settings, db)
    user_id = body.get("user_id", "usr_default")
    event_ids = await connector.handle_event_callback(body, user_id)
    return WebhookResponse(received=True, event_id=event_ids[0] if event_ids else None)


@router.post("/v1/webhooks/slack/test", response_model=WebhookResponse)
async def slack_test_webhook(
    payload: SlackTestPayload,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Test endpoint — accepts a single SlackMessagePayload directly."""
    from src.connectors.slack import SlackMessagePayload

    msg = SlackMessagePayload.model_validate(payload.model_dump())
    connector = _make_slack_connector(settings, db)
    event_id = await connector.process_test_message(msg, user_id)
    return WebhookResponse(received=True, event_id=event_id)


@router.post("/v1/webhooks/whatsapp", response_model=WebhookResponse)
async def whatsapp_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Receive WhatsApp Business API webhook callback."""
    body = await request.json()

    # Handle Meta webhook verification
    if request.method == "GET":
        return {"hub.challenge": body.get("hub.challenge", "")}

    connector = _make_whatsapp_connector(settings, db)
    user_id = body.get("user_id", "usr_default")
    event_ids = await connector.handle_webhook(body, user_id)
    return WebhookResponse(received=True, event_id=event_ids[0] if event_ids else None)


@router.post("/v1/webhooks/whatsapp/test", response_model=WebhookResponse)
async def whatsapp_test_webhook(
    payload: WhatsAppTestPayload,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Test endpoint — accepts a single WhatsApp message payload directly."""
    from src.connectors.whatsapp import WhatsAppMessagePayload

    msg = WhatsAppMessagePayload.model_validate(payload.model_dump())
    connector = _make_whatsapp_connector(settings, db)
    event_id = await connector.process_test_message(msg, user_id)
    return WebhookResponse(received=True, event_id=event_id)


@router.post("/v1/webhooks/generic", response_model=WebhookResponse)
async def generic_webhook(request: Request):
    """Receive generic connector webhook."""
    await request.json()
    return WebhookResponse(received=True)
