"""Push Receiver — validates incoming webhook deliveries and signals perception.

Receives webhook payloads from external providers, verifies signatures, and
signals the PerceptionPolicyService to schedule a poll on the next scheduler
tick. No NormalizedEvent is created here — event ingestion happens through
the real connector → EventProcessor funnel triggered by the wake signal.

Security model (inbound deliveries are untrusted until proven otherwise):
- Verification is **fail-CLOSED and provider-aware** — a delivery is rejected
  unless it carries a valid provider-specific proof of origin. A signed provider
  with no stored secret is rejected (never accepted unsigned).
- Replay defense — Slack uses a 5-minute timestamp window; GitHub dedups on the
  ``X-GitHub-Delivery`` UUID via a short Redis TTL set when Redis is available.
- Deliveries are wake-signals only; no event body is parsed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.sync.webhook_manager import WebhookManager

logger = logging.getLogger(__name__)

# Providers verified via an HMAC over the request body (sha256= hex digest).
_HMAC_PROVIDERS = {"github"}
# Providers verified via Google push channel-token / channel-id headers.
_GOOGLE_PROVIDERS = {"google", "gmail", "calendar"}

# Slack rejects requests whose timestamp is more than 5 minutes off — this is the
# replay window. We enforce the same bound.
_SLACK_REPLAY_WINDOW_S = 300

# Redis TTL for the GitHub delivery-id seen-set (replay dedup window).
_GITHUB_DEDUP_TTL_S = 600


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


def _normalize_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    """Lowercase header keys for case-insensitive lookup; drop non-str values."""
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items() if v is not None}


class PushReceiver:
    """Receives, verifies, and wake-signals webhook deliveries.

    A verified delivery sets ``pending_run=True`` on the matching
    PerceptionState so the scheduler picks up the source on its next tick and
    runs it through the real connector → EventProcessor funnel.  No
    NormalizedEvent row is created here.
    """

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: str,
        callback_base_url: str,
        redis: Any | None = None,
        lag_threshold: int = 0,
    ):
        self._db = db
        self._workspace_id = workspace_id
        self._webhook_manager = WebhookManager(db, workspace_id, callback_base_url)
        self._redis = redis
        # When > 0 and Redis is available, a verified delivery is dropped with
        # ``backpressure`` if the subscription's workspace event stream lag
        # exceeds this. Checked AFTER the subscription row resolves the real
        # workspace (the per-provider route carries no session, so workspace is
        # unknown until lookup). 0 disables the guard.
        self._lag_threshold = lag_threshold

    async def handle_delivery(
        self,
        provider: str,
        subscription_id: str,
        payload: dict,
        signature: str | None = None,
        raw_body: bytes | None = None,
        headers: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        """Process an incoming webhook delivery.

        Steps (in order):
        1. Reject unknown / inactive subscriptions.
        2. Verify provider-specific proof of origin (fail-CLOSED).
        3. Apply replay defense (Slack window / GitHub delivery-id dedup).
        4. Handle Google ``sync`` handshake (ACK without a wake signal).
        5. Signal PerceptionPolicyService to schedule a poll.
        6. Record the successful delivery.
        """
        from sqlalchemy import select

        from src.models.webhook_subscription import WebhookSubscription

        hdrs = _normalize_headers(headers)
        body = raw_body if raw_body is not None else b""

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

        # 2. Verify provider-specific proof of origin — FAIL CLOSED.
        if not self._verify(provider, sub, body, signature, hdrs):
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

        # 3. Replay defense (after authentication, before side effects).
        if await self._is_replayed(provider, subscription_id, hdrs):
            logger.info(
                "webhook_duplicate_delivery",
                extra={"subscription_id": subscription_id, "provider": provider},
            )
            return DeliveryResult(
                accepted=False,
                subscription_id=subscription_id,
                error="duplicate_delivery",
            )

        # 4. Google watch handshake: resource-state=sync is an ACK, not a wake.
        if provider in _GOOGLE_PROVIDERS and hdrs.get("x-goog-resource-state") == "sync":
            await self._webhook_manager.record_delivery(subscription_id)
            logger.info(
                "webhook_sync_handshake_acked",
                extra={"subscription_id": subscription_id, "provider": provider},
            )
            return DeliveryResult(
                accepted=True,
                subscription_id=subscription_id,
                event_id=None,
            )

        # 4b. Backpressure: drop the wake-signal if this workspace's event
        # stream is backlogged. Checked here (not as a route dependency) because
        # the provider route carries no session — the workspace is only known
        # once ``sub`` resolves. Verified-first so an unverified request can't
        # probe lag. Fail-OPEN on a lag-check error (never block legit deliveries).
        if await self._is_backpressured(sub.workspace_id):
            logger.warning(
                "webhook_backpressure_drop",
                extra={"subscription_id": subscription_id, "provider": provider},
            )
            return DeliveryResult(
                accepted=False,
                subscription_id=subscription_id,
                error="backpressure",
            )

        # 5. Signal the perception layer — source is hot; scheduler picks it up next tick
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

        # 6. Record successful delivery
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

    # ------------------------------------------------------------------
    # Backpressure (per-workspace event-stream lag)
    # ------------------------------------------------------------------

    async def _is_backpressured(self, workspace_id: str) -> bool:
        """True if the workspace's event stream lag exceeds the threshold.

        Mirrors ``routes_webhooks._check_backpressure`` but resolves the
        workspace from the subscription instead of a session dependency. Fails
        OPEN (returns False) on any error so a lag-check failure can't drop a
        legitimate, already-verified delivery.
        """
        if self._redis is None or self._lag_threshold <= 0 or not workspace_id:
            return False
        try:
            from src.services.event_bus import EventBus

            bus = EventBus(self._redis)
            lag = await bus.get_stream_lag(bus.event_stream(workspace_id))
            return lag > self._lag_threshold
        except Exception:
            logger.warning("webhook_backpressure_check_failed", exc_info=True)
            return False  # fail open — never block legit deliveries

    # ------------------------------------------------------------------
    # Verification (fail-closed, provider-aware)
    # ------------------------------------------------------------------

    def _verify(
        self,
        provider: str,
        sub: Any,
        body: bytes,
        signature: str | None,
        hdrs: dict[str, str],
    ) -> bool:
        """Return True only if the delivery proves it came from the provider.

        Fail-CLOSED: any missing secret/header or mismatch returns False. The
        caller treats False as a 403 without leaking which check failed.
        """
        secret = sub.secret

        if provider in _GOOGLE_PROVIDERS:
            return self._verify_google(sub, hdrs)

        # Every non-Google provider here is HMAC-signed and REQUIRES a secret.
        if not secret:
            logger.warning(
                "webhook_missing_secret",
                extra={"provider": provider, "subscription_id": sub.subscription_id},
            )
            return False

        if provider == "slack":
            return self._verify_slack(secret, body, signature, hdrs)

        if provider in _HMAC_PROVIDERS:
            return self._verify_github(secret, body, signature)

        # Default/generic: require a body-HMAC over the raw body.
        return self._verify_body_hmac(secret, body, signature)

    @staticmethod
    def _verify_github(secret: str, body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def _verify_body_hmac(secret: str, body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def _verify_slack(
        secret: str,
        body: bytes,
        signature: str | None,
        hdrs: dict[str, str],
    ) -> bool:
        """Slack v0 scheme: sign ``v0:{ts}:{body}`` + enforce 5-min replay window."""
        if not signature:
            return False
        ts = hdrs.get("x-slack-request-timestamp")
        if not ts:
            return False
        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            return False
        if abs(int(time.time()) - ts_int) > _SLACK_REPLAY_WINDOW_S:
            return False  # stale → replay
        base = f"v0:{ts}:".encode() + body
        expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def _verify_google(sub: Any, hdrs: dict[str, str]) -> bool:
        """Google push: channel-token == secret AND channel-id == external_id.

        Google does not HMAC the (empty) body, so verification is header-only.
        Fail-closed if the stored secret/external_id are missing.

        This handles Calendar push (direct ``web_hook`` callback, which carries
        the X-Goog-Channel-* headers). Gmail push is delivered via Pub/Sub with
        no X-Goog-Channel-* headers, so it is NOT accepted here — the Gmail
        Pub/Sub inbound path (OIDC token + envelope parsing) is not yet wired,
        and gmail is intentionally kept poll-only (see _PUSH_PROVIDERS in
        webhook_manager.py).
        """
        secret = sub.secret
        external_id = sub.external_id
        if not secret or not external_id:
            return False
        token = hdrs.get("x-goog-channel-token")
        channel_id = hdrs.get("x-goog-channel-id")
        if not token or not channel_id:
            return False
        token_ok = hmac.compare_digest(token, secret)
        channel_ok = hmac.compare_digest(channel_id, external_id)
        return token_ok and channel_ok

    # ------------------------------------------------------------------
    # Replay defense
    # ------------------------------------------------------------------

    async def _is_replayed(
        self,
        provider: str,
        subscription_id: str,
        hdrs: dict[str, str],
    ) -> bool:
        """Return True if this delivery was already seen (GitHub delivery-id dedup).

        Uses a Redis seen-set with a short TTL when Redis is available. Without
        Redis we cannot dedup across processes, so we fail OPEN here (accept) —
        GitHub's HMAC already authenticated the request, and Slack/Google carry
        their own replay defenses (timestamp window / channel auth).
        """
        if provider != "github" or self._redis is None:
            return False
        delivery_id = hdrs.get("x-github-delivery")
        if not delivery_id:
            return False
        key = f"webhook:seen:github:{subscription_id}:{delivery_id}"
        try:
            # SET key 1 NX EX ttl → truthy only if newly created (first sight).
            created = await self._redis.set(key, "1", nx=True, ex=_GITHUB_DEDUP_TTL_S)
        except Exception:
            logger.warning("webhook_dedup_redis_failed", exc_info=True)
            return False  # fail open on dedup-store failure
        return not created
