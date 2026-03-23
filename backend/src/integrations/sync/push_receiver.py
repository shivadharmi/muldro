"""Push Receiver — validates and routes incoming webhook deliveries.

Receives webhook payloads from external providers, verifies signatures,
normalizes the event, and routes to the event processor.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.sync.webhook_manager import WebhookManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    accepted: bool
    subscription_id: str | None = None
    event_id: str | None = None
    error: str | None = None


class PushReceiver:
    """Receives, verifies, and routes webhook deliveries."""

    def __init__(self, db: AsyncSession, workspace_id: str, callback_base_url: str):
        self._db = db
        self._workspace_id = workspace_id
        self._webhook_manager = WebhookManager(db, workspace_id, callback_base_url)

    async def handle_delivery(
        self,
        provider: str,
        subscription_id: str,
        payload: dict,
        signature: str | None = None,
        raw_body: bytes | None = None,
    ) -> DeliveryResult:
        """Process an incoming webhook delivery."""
        from sqlalchemy import select

        from src.models.webhook_subscription import WebhookSubscription

        # Look up the subscription
        result = await self._db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.subscription_id == subscription_id,
                WebhookSubscription.provider == provider,
            )
        )
        sub = result.scalar_one_or_none()

        if not sub:
            logger.warning(
                "webhook_unknown_subscription",
                extra={"provider": provider, "subscription_id": subscription_id},
            )
            return DeliveryResult(accepted=False, error="unknown_subscription")

        if sub.status != "active":
            return DeliveryResult(
                accepted=False,
                subscription_id=subscription_id,
                error=f"subscription_{sub.status}",
            )

        # Verify signature if secret is set
        if sub.secret and raw_body:
            if not self._verify_signature(sub.secret, raw_body, signature, provider):
                logger.warning(
                    "webhook_signature_mismatch",
                    extra={"subscription_id": subscription_id, "provider": provider},
                )
                await self._webhook_manager.record_failure(subscription_id, "signature_mismatch")
                return DeliveryResult(
                    accepted=False,
                    subscription_id=subscription_id,
                    error="signature_mismatch",
                )

        # Normalize and route the event
        event_id = await self._route_event(sub, payload)

        # Record successful delivery
        await self._webhook_manager.record_delivery(subscription_id)

        # Signal perception policy — source is hot
        try:
            from src.services.perception_policy import PerceptionPolicyService

            policy_svc = PerceptionPolicyService(self._db)
            await policy_svc.request_run(
                workspace_id=sub.workspace_id,
                user_id=sub.user_id,
                source=sub.provider,
                signal_source="webhook",
            )
        except Exception:
            logger.debug("Failed to signal perception from webhook", exc_info=True)

        logger.info(
            "webhook_delivery_accepted",
            extra={
                "subscription_id": subscription_id,
                "provider": provider,
                "event_id": event_id,
            },
        )

        return DeliveryResult(
            accepted=True,
            subscription_id=subscription_id,
            event_id=event_id,
        )

    async def _route_event(self, sub, payload: dict) -> str | None:
        """Normalize the webhook payload and ingest as an event."""
        normalized = _normalize_payload(sub.provider, sub.resource_type, payload)
        if not normalized:
            return None

        from src.models.events import NormalizedEvent
        from src.models.ids import generate_id

        event_id = generate_id("evt")
        event = NormalizedEvent(
            event_id=event_id,
            workspace_id=sub.workspace_id,
            user_id=sub.user_id,
            source=sub.provider,
            event_type=normalized.get("event_type", "webhook_delivery"),
            entity_type=sub.resource_type,
            entity_id=normalized.get("entity_id", sub.resource_id),
            title=normalized.get("title", f"Webhook from {sub.provider}"),
            summary=normalized.get("summary"),
            raw_payload=payload,
            importance_score=normalized.get("importance_score", 0.5),
        )
        self._db.add(event)
        await self._db.flush()
        return event_id

    def _verify_signature(
        self,
        secret: str,
        raw_body: bytes,
        signature: str | None,
        provider: str,
    ) -> bool:
        """Verify webhook signature using provider-specific method."""
        if not signature:
            return False

        if provider == "github":
            expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)

        if provider in ("slack", "linear"):
            expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)

        # Default: raw HMAC-SHA256 comparison
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def _normalize_payload(provider: str, resource_type: str, payload: dict) -> dict | None:
    """Extract normalized event data from a provider-specific webhook payload."""
    if provider == "github":
        return _normalize_github(payload)
    if provider == "gmail":
        return _normalize_gmail(payload)
    if provider == "slack":
        return _normalize_slack(payload)
    if provider == "calendar":
        return _normalize_calendar(payload)
    return {
        "event_type": "webhook_delivery",
        "entity_id": payload.get("id", ""),
        "title": f"Webhook from {provider}",
        "summary": str(payload)[:500],
        "importance_score": 0.5,
    }


def _normalize_github(payload: dict) -> dict:
    action = payload.get("action", "")
    if "pull_request" in payload:
        pr = payload["pull_request"]
        return {
            "event_type": f"pr_{action}",
            "entity_id": str(pr.get("number", "")),
            "title": f"PR #{pr.get('number')}: {pr.get('title', '')}",
            "summary": f"PR {action} in {payload.get('repository', {}).get('full_name', '')}",
            "importance_score": 0.7 if action in ("opened", "closed") else 0.4,
        }
    if "issue" in payload:
        issue = payload["issue"]
        return {
            "event_type": f"issue_{action}",
            "entity_id": str(issue.get("number", "")),
            "title": f"Issue #{issue.get('number')}: {issue.get('title', '')}",
            "summary": f"Issue {action} in {payload.get('repository', {}).get('full_name', '')}",
            "importance_score": 0.6 if action in ("opened", "closed") else 0.3,
        }
    return {
        "event_type": f"github_{action}",
        "entity_id": str(payload.get("repository", {}).get("id", "")),
        "title": f"GitHub event: {action}",
        "importance_score": 0.3,
    }


def _normalize_gmail(payload: dict) -> dict:
    return {
        "event_type": "email_received",
        "entity_id": payload.get("historyId", ""),
        "title": "New Gmail activity",
        "summary": f"History ID: {payload.get('historyId', '')}",
        "importance_score": 0.6,
    }


def _normalize_slack(payload: dict) -> dict:
    event = payload.get("event", {})
    event_type = event.get("type", "message")
    return {
        "event_type": f"slack_{event_type}",
        "entity_id": event.get("channel", ""),
        "title": f"Slack {event_type}",
        "summary": event.get("text", "")[:300],
        "importance_score": 0.5,
    }


def _normalize_calendar(payload: dict) -> dict:
    return {
        "event_type": "calendar_change",
        "entity_id": payload.get("resourceId", ""),
        "title": "Calendar update",
        "summary": f"Resource: {payload.get('resourceUri', '')}",
        "importance_score": 0.5,
    }
