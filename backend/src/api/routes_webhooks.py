"""Webhook endpoints — generic passthrough + provider-specific webhooks.

Includes:
- /v1/webhooks/generic — backwards-compatible generic passthrough
- /v1/webhooks/whatsapp — Meta WhatsApp Business API webhooks
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
    workspace_id: str = Depends(get_current_workspace_id),
    settings: Settings = Depends(get_settings),
):
    """Reject webhooks when the current workspace's event stream lag is too high.

    Scoped to the requesting workspace's stream to avoid throttling healthy
    tenants because another tenant is backlogged.
    """
    redis = getattr(request.app.state, "redis", None)
    if redis and settings.webhook_lag_threshold > 0:
        from src.services.event_bus import EventBus

        bus = EventBus(redis)
        try:
            stream = bus.event_stream(workspace_id)
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


# ── Provider callback webhooks (wake-signal) ─────────────────


def _extract_signature(request: Request) -> str | None:
    """Pull the provider signature header (varies by provider)."""
    headers = request.headers
    return (
        headers.get("X-Hub-Signature-256")  # GitHub, Meta
        or headers.get("X-Slack-Signature")  # Slack
        or headers.get("X-Signature-256")
        or headers.get("X-Signature")
    )


@router.post("/v1/webhooks/{provider}/{subscription_id}")
async def provider_webhook(
    provider: str,
    subscription_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Receive a provider webhook delivery and signal perception.

    This is the callback URL WebhookManager registers with external providers.
    The delivery is a wake-signal only: PushReceiver verifies the HMAC signature
    and sets ``pending_run`` on the matching PerceptionState so the scheduler
    polls the source through the real connector → EventProcessor funnel on its
    next tick. No NormalizedEvent is created here.

    Unauthenticated by design (providers cannot carry a user session); the HMAC
    signature on the subscription is the security boundary.
    """
    # PushReceiver authoritatively resolves workspace/user from the subscription
    # row; the constructor's workspace_id/callback_base_url are unused on the
    # inbound delivery path (record_delivery/record_failure key on sub id alone).
    from src.integrations.sync.push_receiver import PushReceiver

    raw_body = await request.body()
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    receiver = PushReceiver(db, workspace_id="", callback_base_url="")
    result = await receiver.handle_delivery(
        provider=provider,
        subscription_id=subscription_id,
        payload=payload,
        signature=_extract_signature(request),
        raw_body=raw_body,
    )

    if not result.accepted:
        if result.error == "unknown_subscription":
            raise HTTPException(status_code=404, detail=result.error)
        if result.error == "signature_mismatch":
            raise HTTPException(status_code=403, detail=result.error)
        raise HTTPException(status_code=400, detail=result.error or "rejected")

    await db.commit()
    return {"status": "accepted", "subscription_id": subscription_id}


# ── WhatsApp Webhooks (Meta Business API) ────────────────────


@router.get("/v1/webhooks/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    settings: Settings = Depends(get_settings),
):
    """Meta webhook verification challenge (GET)."""
    expected_token = getattr(settings, "whatsapp_verify_token", "")
    if hub_mode == "subscribe" and hub_verify_token == expected_token:
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
    whatsapp_secret = getattr(settings, "whatsapp_app_secret", "")
    if whatsapp_secret:
        expected = (
            "sha256=" + hmac.new(whatsapp_secret.encode(), raw_body, hashlib.sha256).hexdigest()
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
