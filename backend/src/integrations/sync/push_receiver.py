"""Push Receiver — validates incoming webhook deliveries and signals perception.

Receives webhook payloads from external providers, verifies signatures, and
signals the PerceptionPolicyService to schedule a poll on the next scheduler
tick. No NormalizedEvent is created here — event ingestion happens through
the real connector → EventProcessor funnel triggered by the wake signal.
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
    """Result of a single webhook delivery attempt.

    ``event_id`` is always ``None``: webhooks are wake-signals only.
    NormalizedEvents are created later by the scheduler-triggered poll through
    EventProcessor, not by the webhook handler itself.
    """

    accepted: bool
    subscription_id: str | None = None
    event_id: str | None = None
    error: str | None = None


class PushReceiver:
    """Receives, verifies, and wake-signals webhook deliveries.

    A verified delivery sets ``pending_run=True`` on the matching
    PerceptionState so the scheduler picks up the source on its next tick and
    runs it through the real connector → EventProcessor funnel.  No
    NormalizedEvent row is created here.
    """

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
        """Process an incoming webhook delivery.

        Steps (in order):
        1. Reject unknown / inactive subscriptions.
        2. Verify HMAC signature (when a secret is configured).
        3. Signal PerceptionPolicyService to schedule a poll.
        4. Record the successful delivery.
        """
        from sqlalchemy import select

        from src.models.webhook_subscription import WebhookSubscription

        # 1. Look up the subscription
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

        # 2. Verify signature if a secret is configured
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

        # 3. Signal the perception layer — source is hot; scheduler picks it up next tick
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
            logger.warning(
                "webhook_wake_signal_failed",
                extra={"subscription_id": subscription_id, "provider": provider},
                exc_info=True,
            )
            await self._webhook_manager.record_failure(subscription_id, "wake_signal_failed")
            return DeliveryResult(
                accepted=False,
                subscription_id=subscription_id,
                error="wake_signal_failed",
            )

        # 4. Record successful delivery
        await self._webhook_manager.record_delivery(subscription_id)

        logger.info(
            "webhook_delivery_accepted",
            extra={
                "subscription_id": subscription_id,
                "provider": provider,
            },
        )

        # event_id is always None — see DeliveryResult docstring.
        return DeliveryResult(
            accepted=True,
            subscription_id=subscription_id,
            event_id=None,
        )

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

        if provider == "slack":
            expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)

        # Default: raw HMAC-SHA256 comparison
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
