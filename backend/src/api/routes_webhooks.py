"""Webhook endpoints — generic passthrough + provider-specific webhooks.

Includes:
- /v1/webhooks/generic — backwards-compatible generic passthrough
- /v1/webhooks/whatsapp — Meta WhatsApp Business API webhooks
- /v1/webhooks/twilio/sms — Twilio incoming SMS webhooks
"""

import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.routes_events import _make_event_processor
from src.api.schemas import EventIngestResponse
from src.config.settings import Settings, get_settings
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

router = APIRouter()


async def _check_backpressure(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """Reject webhooks when the current user's event stream lag is too high.

    Scoped to the requesting user's stream to avoid throttling healthy tenants
    because another tenant is backlogged.
    """
    redis = getattr(request.app.state, "redis", None)
    if redis and settings.webhook_lag_threshold > 0:
        from src.services.event_bus import EventBus

        bus = EventBus(redis)
        try:
            stream = bus.event_stream(user_id)
            lag = await bus.get_stream_lag(stream)
            if lag > settings.webhook_lag_threshold:
                raise HTTPException(
                    status_code=429,
                    detail=f"Event queue backlogged ({lag} pending), retry later",
                )
        except HTTPException:
            raise
        except Exception:
            pass  # Don't block webhooks if lag check itself fails


@router.post(
    "/v1/webhooks/generic",
    response_model=EventIngestResponse,
    dependencies=[Depends(_check_backpressure)],
)
async def generic_webhook(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
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

    redis = getattr(request.app.state, "redis", None)
    processor = await _make_event_processor(settings, db, redis=redis)
    event_id = await processor.process(raw, user_id, workspace_id)

    if event_id is None:
        return EventIngestResponse(event_id=None, status="duplicate", importance_score=None)

    return EventIngestResponse(event_id=event_id, status="processed", importance_score=None)


# ── WhatsApp Webhooks (Meta Business API) ────────────────────


@router.get("/v1/webhooks/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    settings: Settings = Depends(get_settings),
):
    """Meta webhook verification challenge (GET)."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/v1/webhooks/whatsapp")
async def whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str = Header("", alias="X-Hub-Signature-256"),
    settings: Settings = Depends(get_settings),
):
    """Receive WhatsApp incoming messages via Meta webhook."""
    raw_body = await request.body()

    # Verify signature
    if settings.whatsapp_app_secret:
        expected = (
            "sha256="
            + hmac.new(settings.whatsapp_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(status_code=403, detail="Invalid signature")

    body = await request.json()

    # Forward to WhatsApp connector's handle_webhook if registered
    from src.connectors.base import CONNECTOR_REGISTRY

    connector_cls = CONNECTOR_REGISTRY.get("whatsapp")
    if connector_cls:
        instance = connector_cls(settings=settings)
        events = await instance.handle_webhook(body)
        return {"status": "ok", "events": len(events)}

    return {"status": "ok", "events": 0}


# ── Twilio SMS Webhook ───────────────────────────────────────


@router.post("/v1/webhooks/twilio/sms")
async def twilio_sms_webhook(
    request: Request,
    x_twilio_signature: str = Header("", alias="X-Twilio-Signature"),
    settings: Settings = Depends(get_settings),
):
    """Receive incoming SMS from Twilio."""
    form_data = await request.form()
    params = dict(form_data)

    # Verify Twilio signature
    if settings.twilio_auth_token:
        url = str(request.url)
        # Twilio signature = HMAC-SHA1(auth_token, url + sorted params)
        sorted_params = "".join(f"{k}{params[k]}" for k in sorted(params))
        import base64

        signature = base64.b64encode(
            hmac.new(
                settings.twilio_auth_token.encode(),
                (url + sorted_params).encode(),
                hashlib.sha1,
            ).digest()
        ).decode()
        if not hmac.compare_digest(signature, x_twilio_signature):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # Forward to Twilio connector's handle_webhook if registered
    from src.connectors.base import CONNECTOR_REGISTRY

    connector_cls = CONNECTOR_REGISTRY.get("sms")
    if connector_cls:
        instance = connector_cls(settings=settings)
        await instance.handle_webhook(params)
        return Response(content="<Response></Response>", media_type="application/xml")

    return Response(content="<Response></Response>", media_type="application/xml")
