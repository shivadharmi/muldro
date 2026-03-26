"""Webhook Manager — register, renew, and deactivate push subscriptions.

Manages the lifecycle of webhook subscriptions across providers:
- Register new webhooks with external services
- Renew expiring subscriptions
- Handle delivery confirmations and failures
- Deactivate stale or revoked subscriptions
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ids import generate_id
from src.models.webhook_subscription import WebhookSubscription

logger = logging.getLogger(__name__)

RENEWAL_BUFFER_HOURS = 6
MAX_CONSECUTIVE_FAILURES = 5


class WebhookManager:
    """Manages webhook subscription lifecycle."""

    def __init__(self, db: AsyncSession, workspace_id: str, callback_base_url: str):
        self._db = db
        self._workspace_id = workspace_id
        self._callback_base_url = callback_base_url.rstrip("/")

    async def register(
        self,
        user_id: str,
        provider: str,
        resource_type: str,
        resource_id: str,
        ttl_hours: int = 168,
        config: dict | None = None,
    ) -> WebhookSubscription:
        """Register a new webhook subscription."""
        sub_id = generate_id("whsub")
        secret = secrets.token_urlsafe(32)
        callback_url = f"{self._callback_base_url}/v1/webhooks/{provider}/{sub_id}"

        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

        sub = WebhookSubscription(
            subscription_id=sub_id,
            workspace_id=self._workspace_id,
            user_id=user_id,
            provider=provider,
            resource_type=resource_type,
            resource_id=resource_id,
            callback_url=callback_url,
            secret=secret,
            status="active",
            expires_at=expires_at,
            config=config,
        )
        self._db.add(sub)
        await self._db.flush()

        logger.info(
            "webhook_registered",
            extra={
                "subscription_id": sub_id,
                "provider": provider,
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
        )
        return sub

    async def deactivate(self, subscription_id: str) -> None:
        """Deactivate a webhook subscription."""
        await self._db.execute(
            update(WebhookSubscription)
            .where(
                WebhookSubscription.subscription_id == subscription_id,
                WebhookSubscription.workspace_id == self._workspace_id,
            )
            .values(status="expired", updated_at=datetime.now(timezone.utc))
        )
        logger.info("webhook_deactivated", extra={"subscription_id": subscription_id})

    async def record_delivery(self, subscription_id: str) -> None:
        """Record a successful webhook delivery."""
        await self._db.execute(
            update(WebhookSubscription)
            .where(WebhookSubscription.subscription_id == subscription_id)
            .values(
                last_delivery_at=datetime.now(timezone.utc),
                delivery_count=WebhookSubscription.delivery_count + 1,
                consecutive_failures=0,
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def record_failure(self, subscription_id: str, error: str) -> None:
        """Record a delivery failure. Auto-pauses after MAX_CONSECUTIVE_FAILURES."""
        result = await self._db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.subscription_id == subscription_id
            )
        )
        sub = result.scalar_one_or_none()
        if not sub:
            return

        sub.consecutive_failures += 1
        sub.last_error = error[:1000]
        sub.updated_at = datetime.now(timezone.utc)

        if sub.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            sub.status = "failed"
            logger.warning(
                "webhook_auto_paused",
                extra={"subscription_id": subscription_id, "failures": sub.consecutive_failures},
            )

    async def get_active_subscriptions(
        self, provider: str | None = None
    ) -> list[WebhookSubscription]:
        """Get all active subscriptions, optionally filtered by provider."""
        stmt = select(WebhookSubscription).where(
            WebhookSubscription.workspace_id == self._workspace_id,
            WebhookSubscription.status == "active",
        )
        if provider:
            stmt = stmt.where(WebhookSubscription.provider == provider)

        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_expiring_subscriptions(self) -> list[WebhookSubscription]:
        """Get subscriptions expiring within RENEWAL_BUFFER_HOURS."""
        threshold = datetime.now(timezone.utc) + timedelta(hours=RENEWAL_BUFFER_HOURS)
        result = await self._db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == self._workspace_id,
                WebhookSubscription.status == "active",
                WebhookSubscription.expires_at.isnot(None),
                WebhookSubscription.expires_at <= threshold,
            )
        )
        return list(result.scalars().all())

    async def renew(self, subscription_id: str, new_ttl_hours: int = 168) -> None:
        """Renew an expiring subscription."""
        new_expiry = datetime.now(timezone.utc) + timedelta(hours=new_ttl_hours)
        await self._db.execute(
            update(WebhookSubscription)
            .where(
                WebhookSubscription.subscription_id == subscription_id,
                WebhookSubscription.workspace_id == self._workspace_id,
            )
            .values(
                expires_at=new_expiry,
                status="active",
                updated_at=datetime.now(timezone.utc),
            )
        )
        logger.info(
            "webhook_renewed",
            extra={"subscription_id": subscription_id, "expires_at": new_expiry.isoformat()},
        )

    async def get_by_external_id(self, external_id: str) -> WebhookSubscription | None:
        """Look up a subscription by its external (provider-assigned) ID."""
        result = await self._db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_sources_with_push(self) -> set[str]:
        """Get the set of providers that have active push subscriptions."""
        result = await self._db.execute(
            select(WebhookSubscription.provider)
            .where(
                WebhookSubscription.workspace_id == self._workspace_id,
                WebhookSubscription.status == "active",
            )
            .distinct()
        )
        return {row[0] for row in result.all()}
