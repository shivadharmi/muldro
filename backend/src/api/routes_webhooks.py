"""Webhook endpoints — receive external service pushes (Gmail, Calendar, Slack)."""

from fastapi import APIRouter, Request

from src.api.schemas import WebhookResponse

router = APIRouter()


@router.post("/v1/webhooks/gmail", response_model=WebhookResponse)
async def gmail_webhook(request: Request):
    """Receive Gmail push notification (from Google Pub/Sub).

    This does NOT require user auth — authenticated via shared secret
    from the OpenClaw plugin route or Google's push mechanism.
    """
    _body = await request.json()  # noqa: F841 — will be used when connector is wired
    # TODO: Wire to Gmail connector service
    return WebhookResponse(received=True)


@router.post("/v1/webhooks/calendar", response_model=WebhookResponse)
async def calendar_webhook(request: Request):
    """Receive Calendar change notification."""
    _body = await request.json()  # noqa: F841
    # TODO: Wire to Calendar connector service
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
