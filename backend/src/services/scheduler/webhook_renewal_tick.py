"""Webhook push-channel renewal tick.

Google push channels (Gmail users.watch / Calendar events.watch) expire in
<=7 days, so registered subscriptions must be re-watched well before
``expires_at``. This tick finds active subscriptions inside the renewal buffer
(``RENEWAL_BUFFER_HOURS``) and re-registers them via ``WebhookManager.renew``,
which re-calls the provider and updates external_id/secret/expires_at.

Gated: a complete no-op unless ``settings.webhooks_configured`` (master switch
+ public callback base URL). On a default, infra-free deployment the table is
empty and this tick never queries or calls a provider — the system stays
poll-only.
"""

import logging

logger = logging.getLogger(__name__)


class WebhookRenewalTickMixin:
    """Renews push subscriptions approaching expiry."""

    async def _tick_webhook_renewal(self, factory) -> None:
        """Re-watch subscriptions inside the renewal buffer (best-effort)."""
        if not getattr(self._settings, "webhooks_configured", False):
            return  # poll-only deployment — nothing to renew

        try:
            from datetime import datetime, timedelta, timezone

            from sqlalchemy import select

            from src.integrations.sync.webhook_manager import (
                RENEWAL_BUFFER_HOURS,
                WebhookManager,
            )
            from src.models.webhook_subscription import WebhookSubscription
            from src.services.oauth_manager import OAuthManager

            threshold = datetime.now(timezone.utc) + timedelta(hours=RENEWAL_BUFFER_HOURS)

            async with factory() as db:
                # Cross-workspace sweep: all active push subs near expiry.
                result = await db.execute(
                    select(WebhookSubscription).where(
                        WebhookSubscription.status == "active",
                        WebhookSubscription.expires_at.isnot(None),
                        WebhookSubscription.expires_at <= threshold,
                    )
                )
                expiring = list(result.scalars().all())
                if not expiring:
                    return

                oauth_mgr = OAuthManager(
                    factory,
                    encryption_key=getattr(self._settings, "oauth_encryption_key", ""),
                    settings=self._settings,
                )
                renewed = 0
                for sub in expiring:
                    mgr = WebhookManager(
                        db,
                        sub.workspace_id,
                        self._settings.webhook_callback_base_url,
                        settings=self._settings,
                        oauth_manager=oauth_mgr,
                    )
                    try:
                        await mgr.renew(sub.subscription_id)
                        renewed += 1
                    except Exception:
                        logger.warning(
                            "Webhook renew failed for %s",
                            sub.subscription_id,
                            exc_info=True,
                        )

                await db.commit()
                if renewed:
                    logger.info("Webhook renewal tick: %d subscriptions renewed", renewed)
        except Exception:
            logger.warning("Webhook renewal tick error", exc_info=True)
