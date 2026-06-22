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

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ids import generate_id
from src.models.webhook_subscription import WebhookSubscription

logger = logging.getLogger(__name__)

RENEWAL_BUFFER_HOURS = 6
MAX_CONSECUTIVE_FAILURES = 5

# Providers we can register real push channels for. Anything else stays poll-only.
#
# Gmail is deliberately EXCLUDED: Gmail push arrives via Pub/Sub (no
# X-Goog-Channel-* headers), and the inbound PushReceiver._verify_google is
# header-based and would reject it. Registering a Gmail watch would therefore
# create a channel the receiver can never accept. Keep Gmail poll-only and
# re-add it here once the Pub/Sub inbound verification path (OIDC token +
# envelope parsing) is implemented. (_gmail_watch is retained but unreachable
# from register() until then — see its docstring.)
_PUSH_PROVIDERS = {"calendar"}


class _ChannelResult:
    """Outcome of a provider watch() call.

    ``external_id``/``secret``/``expires_at`` are written onto the
    WebhookSubscription row. ``config`` holds provider-specific bookkeeping that
    must survive (e.g. Google's ``resourceId``, needed alongside the channel id
    to call ``channels.stop``). ``None`` everywhere means "fell back to poll mode".
    """

    __slots__ = ("external_id", "secret", "expires_at", "config")

    def __init__(
        self,
        external_id: str | None = None,
        secret: str | None = None,
        expires_at: datetime | None = None,
        config: dict | None = None,
    ):
        self.external_id = external_id
        self.secret = secret
        self.expires_at = expires_at
        self.config = config


class WebhookManager:
    """Manages webhook subscription lifecycle.

    ``settings`` and ``oauth_manager`` are OPTIONAL. When absent — or when
    ``settings.webhooks_configured`` is False — ``register``/``renew`` never call
    a provider API: only the DB row is written and the source stays poll-only.
    This keeps the default, infra-free deployment behaving exactly as before.
    """

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: str,
        callback_base_url: str,
        settings=None,
        oauth_manager=None,
    ):
        self._db = db
        self._workspace_id = workspace_id
        self._callback_base_url = callback_base_url.rstrip("/")
        self._settings = settings
        self._oauth = oauth_manager

    def _push_enabled(self) -> bool:
        """True when this manager may create real provider channels."""
        return bool(
            self._settings is not None
            and getattr(self._settings, "webhooks_configured", False)
            and self._oauth is not None
        )

    async def register(
        self,
        user_id: str,
        provider: str,
        resource_type: str,
        resource_id: str,
        ttl_hours: int = 168,
        config: dict | None = None,
    ) -> WebhookSubscription:
        """Register a webhook subscription.

        Idempotent: if an active, not-near-expiry subscription already exists for
        ``(workspace, provider, resource_type, resource_id)`` it is reused rather
        than creating a duplicate provider channel.

        When push is configured (``settings.webhooks_configured`` + an
        oauth_manager + a supported provider) this calls the provider's watch API
        and persists the returned channel id / token / expiration. Otherwise it
        writes a DB-only row and the source stays in poll mode.
        """
        # Idempotency only applies to the push-aware path. Legacy 3-arg callers
        # (no settings) keep the original always-create contract.
        if self._settings is not None:
            existing = await self._find_active(provider, resource_type, resource_id)
            if existing is not None and not self._near_expiry(existing.expires_at):
                logger.info(
                    "webhook_register_reused",
                    extra={
                        "subscription_id": existing.subscription_id,
                        "provider": provider,
                        "resource_id": resource_id,
                    },
                )
                return existing

        sub_id = generate_id("whsub")
        secret = secrets.token_urlsafe(32)
        callback_url = f"{self._callback_base_url}/v1/webhooks/{provider}/{sub_id}"
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

        external_id: str | None = None
        status = "active"

        if self._push_enabled() and provider in _PUSH_PROVIDERS:
            channel = await self._provider_watch(
                user_id=user_id,
                provider=provider,
                resource_id=resource_id,
                channel_id=sub_id,
                callback_url=callback_url,
                token=secret,
            )
            if channel is None:
                status = "failed"
            else:
                external_id = channel.external_id
                if channel.secret is not None:
                    secret = channel.secret
                if channel.expires_at is not None:
                    expires_at = channel.expires_at
                # Merge provider bookkeeping (e.g. Google resourceId) into the
                # caller-supplied config so channels.stop has what it needs.
                if channel.config:
                    config = {**(config or {}), **channel.config}

        sub = WebhookSubscription(
            subscription_id=sub_id,
            workspace_id=self._workspace_id,
            user_id=user_id,
            provider=provider,
            resource_type=resource_type,
            resource_id=resource_id,
            callback_url=callback_url,
            secret=secret,
            external_id=external_id,
            status=status,
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
                "push": external_id is not None,
            },
        )
        return sub

    async def _find_active(
        self, provider: str, resource_type: str, resource_id: str
    ) -> WebhookSubscription | None:
        """Find an existing active subscription for this exact resource."""
        result = await self._db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == self._workspace_id,
                WebhookSubscription.provider == provider,
                WebhookSubscription.resource_type == resource_type,
                WebhookSubscription.resource_id == resource_id,
                WebhookSubscription.status == "active",
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _near_expiry(expires_at: datetime | None) -> bool:
        """True if a subscription has no expiry info or is inside the renewal buffer."""
        if expires_at is None:
            return False
        threshold = datetime.now(timezone.utc) + timedelta(hours=RENEWAL_BUFFER_HOURS)
        return expires_at <= threshold

    async def _provider_watch(
        self,
        user_id: str,
        provider: str,
        resource_id: str,
        channel_id: str,
        callback_url: str,
        token: str,
    ) -> _ChannelResult | None:
        """Call the provider's watch API. Returns None on any failure (→ poll)."""
        # Gmail push needs a Pub/Sub topic; skip (poll mode) before any token
        # fetch when it isn't configured.
        if provider == "gmail" and not (getattr(self._settings, "gmail_pubsub_topic", "") or ""):
            logger.info("webhook_gmail_skip_no_topic")
            return None

        access_token = await self._oauth.get_valid_token(user_id, "google")
        if not access_token:
            logger.info("webhook_watch_no_token", extra={"provider": provider, "user_id": user_id})
            return None

        try:
            if provider == "gmail":
                return await self._gmail_watch(access_token, channel_id, token)
            if provider == "calendar":
                return await self._calendar_watch(
                    access_token, resource_id, channel_id, callback_url, token
                )
        except Exception:
            logger.warning(
                "webhook_watch_failed",
                extra={"provider": provider, "user_id": user_id},
                exc_info=True,
            )
            return None
        return None

    async def _gmail_watch(
        self, access_token: str, channel_id: str, token: str
    ) -> _ChannelResult | None:
        """Gmail users.watch — push delivery goes via Pub/Sub, not a callback URL.

        DEFERRED / currently unreachable from ``register()``: gmail is not in
        ``_PUSH_PROVIDERS`` because Pub/Sub deliveries carry no X-Goog-Channel-*
        headers, so the header-based inbound ``_verify_google`` would reject
        them. This method is retained (and unit-tested directly) so the watch
        call is ready to wire up once the Pub/Sub inbound verification path
        (OIDC token + envelope parsing) lands; re-add ``gmail`` to
        ``_PUSH_PROVIDERS`` at that point.

        The minted ``channel_id`` is stored as our external_id and ``token`` as
        the channel verification secret; Gmail itself doesn't echo these (delivery
        is via the Pub/Sub push subscription), but keeping them lets the inbound
        receiver correlate and verify uniformly across providers.
        """
        topic = getattr(self._settings, "gmail_pubsub_topic", "") or ""
        if not topic:
            logger.info("webhook_gmail_skip_no_topic")
            return None

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/watch",
                json={"topicName": topic, "labelIds": ["INBOX"]},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
        if resp.status_code != 200:
            logger.warning("gmail_watch returned %d: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        return _ChannelResult(
            external_id=channel_id,
            secret=token,
            expires_at=_parse_google_expiration(data.get("expiration")),
        )

    async def _calendar_watch(
        self,
        access_token: str,
        resource_id: str,
        channel_id: str,
        callback_url: str,
        token: str,
    ) -> _ChannelResult | None:
        """Calendar events.watch — provider posts to our callback URL directly.

        Per Google's push spec, ``X-Goog-Channel-Id`` on inbound deliveries
        echoes the watch request ``id`` (our minted ``channel_id``), while the
        ``resourceId`` is surfaced separately as ``X-Goog-Resource-Id``. So we
        store ``channel_id`` as ``external_id`` (what ``_verify_google`` compares
        the X-Goog-Channel-Id header against — matching the Gmail path) and keep
        the ``resourceId`` in ``config`` because ``channels.stop`` needs BOTH the
        channel id and the resourceId. Storing resourceId in external_id (the
        former behavior) made every real Calendar push fail verification (403).
        """
        calendar = resource_id or "primary"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar}/events/watch",
                json={
                    "id": channel_id,
                    "type": "web_hook",
                    "address": callback_url,
                    "token": token,
                },
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
        if resp.status_code not in (200, 201):
            logger.warning("calendar_watch returned %d: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        return _ChannelResult(
            external_id=channel_id,
            secret=token,
            expires_at=_parse_google_expiration(data.get("expiration")),
            config={"resource_id": data.get("resourceId")},
        )

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
        """Renew an expiring subscription.

        When push is configured this re-calls the provider's watch API (Google
        channels can only be extended by re-watching, which mints a fresh channel)
        and updates external_id/secret/expires_at on the row. Without push config
        it falls back to a DB-only expiry bump (poll-only / legacy behavior).
        """
        if not self._push_enabled():
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
                "webhook_renewed_db_only",
                extra={"subscription_id": subscription_id, "expires_at": new_expiry.isoformat()},
            )
            return

        result = await self._db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.subscription_id == subscription_id,
                WebhookSubscription.workspace_id == self._workspace_id,
            )
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            logger.info("webhook_renew_missing", extra={"subscription_id": subscription_id})
            return

        if sub.provider not in _PUSH_PROVIDERS:
            # Nothing to re-watch (e.g. a poll-only row); just bump expiry.
            sub.expires_at = datetime.now(timezone.utc) + timedelta(hours=new_ttl_hours)
            sub.status = "active"
            sub.updated_at = datetime.now(timezone.utc)
            return

        # Mint a fresh channel id/token and re-watch the provider.
        # TODO(stop-on-rotate): re-watching mints a NEW Google channel; the OLD
        # channel is left running until it expires on its own. To stop it
        # immediately we'd call channels.stop with the old channel id
        # (sub.external_id) AND the old resourceId (sub.config["resource_id"]) —
        # both are persisted for exactly this. Not implemented yet; the old
        # channel simply lapses at its original expiry.
        new_channel_id = generate_id("whsub")
        new_token = secrets.token_urlsafe(32)
        callback_url = f"{self._callback_base_url}/v1/webhooks/{sub.provider}/{new_channel_id}"
        channel = await self._provider_watch(
            user_id=sub.user_id,
            provider=sub.provider,
            resource_id=sub.resource_id,
            channel_id=new_channel_id,
            callback_url=callback_url,
            token=new_token,
        )
        if channel is None:
            # NOTE: expires_at is deliberately NOT bumped on failure — leaving the
            # original (near) expiry means the renewal tick keeps re-selecting this
            # row and retrying each tick (up to MAX_CONSECUTIVE_FAILURES) until a
            # watch succeeds or the row is marked "failed".
            sub.consecutive_failures = (sub.consecutive_failures or 0) + 1
            sub.last_error = "renew_watch_failed"
            sub.updated_at = datetime.now(timezone.utc)
            if sub.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                sub.status = "failed"
            logger.warning(
                "webhook_renew_failed",
                extra={"subscription_id": subscription_id, "provider": sub.provider},
            )
            return

        sub.external_id = channel.external_id
        sub.secret = channel.secret if channel.secret is not None else new_token
        if channel.config:
            # Persist the rotated channel's resourceId (needed for channels.stop).
            sub.config = {**(sub.config or {}), **channel.config}
        if channel.expires_at is not None:
            sub.expires_at = channel.expires_at
        else:
            sub.expires_at = datetime.now(timezone.utc) + timedelta(hours=new_ttl_hours)
        sub.status = "active"
        sub.consecutive_failures = 0
        sub.last_error = None
        sub.updated_at = datetime.now(timezone.utc)
        logger.info(
            "webhook_renewed",
            extra={
                "subscription_id": subscription_id,
                "provider": sub.provider,
                "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
            },
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


def _parse_google_expiration(raw) -> datetime | None:
    """Parse a Google watch ``expiration`` (epoch millis as str/int) to UTC datetime."""
    if raw is None:
        return None
    try:
        millis = int(raw)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
