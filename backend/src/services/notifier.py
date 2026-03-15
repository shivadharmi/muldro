"""Notification coordinator with deduplication and surface-aware delivery.

Routes notifications to the right surface(s) based on type:
- approval_request: Send to ALL active surfaces (user can act from any)
- info_update: Send to preferred surface only
- critical_alert: Send to ALL surfaces + ensure delivery

Deduplicates across surfaces so the user doesn't see the same notification twice.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from ulid import ULID

from src.services.surface_registry import SurfaceRegistry

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    notification_id: str
    user_id: str
    type: str  # approval_request, info_update, critical_alert, briefing
    title: str
    body: str
    data: dict  # Arbitrary payload (approval_id, event_id, etc.)
    created_at: str


class Notifier:
    """Coordinates notification delivery across surfaces."""

    def __init__(
        self,
        surface_registry: SurfaceRegistry,
        redis=None,
        telegram_sender=None,
        websocket_sender=None,
    ):
        self._registry = surface_registry
        self._redis = redis
        self._telegram_sender = telegram_sender
        self._ws_sender = websocket_sender
        # Track delivered notifications for dedup
        self._delivered: dict[str, set[str]] = {}

    async def notify(
        self,
        user_id: str,
        notification_type: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> dict:
        """Send a notification to the appropriate surface(s).

        Returns delivery status per surface.
        """
        notification = Notification(
            notification_id=f"notif_{ULID()}",
            user_id=user_id,
            type=notification_type,
            title=title,
            body=body,
            data=data or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        surfaces = await self._registry.get_active_surfaces(user_id)
        if not surfaces:
            # No active surfaces — queue for later delivery
            logger.warning(
                "no_active_surfaces",
                extra={"user_id": user_id, "notification_id": notification.notification_id},
            )
            return {"status": "queued", "surfaces": []}

        results = {}

        if notification_type in ("approval_request", "critical_alert"):
            # Send to ALL active surfaces
            for surface in surfaces:
                result = await self._deliver(surface, notification)
                results[surface] = result
        else:
            # Send to preferred surface only
            preferred = await self._registry.get_preferred_surface(user_id)
            if preferred:
                result = await self._deliver(preferred, notification)
                results[preferred] = result
                # Mark as delivered so other surfaces can pull on demand
                await self._mark_delivered(notification.notification_id, preferred)

        logger.info(
            "notification_sent",
            extra={
                "notification_id": notification.notification_id,
                "type": notification_type,
                "surfaces": list(results.keys()),
            },
        )
        return {"status": "sent", "surfaces": results}

    async def on_action_taken(
        self,
        user_id: str,
        notification_id: str,
        surface: str,
    ) -> None:
        """When user acts on one surface, notify others to update.

        E.g., user approves on Telegram -> web dashboard updates status.
        """
        if self._redis:
            message = json.dumps(
                {
                    "type": "notification_resolved",
                    "notification_id": notification_id,
                    "resolved_on": surface,
                }
            )
            await self._redis.publish(f"jarvis:surface_sync:{user_id}", message)

        logger.info(
            "notification_resolved",
            extra={
                "notification_id": notification_id,
                "resolved_on": surface,
                "user_id": user_id,
            },
        )

    async def _deliver(self, surface: str, notification: Notification) -> dict:
        """Deliver a notification to a specific surface."""
        try:
            if surface == "telegram":
                return await self._deliver_telegram(notification)
            elif surface == "web":
                return await self._deliver_web(notification)
            else:
                return {"status": "unsupported_surface"}
        except Exception as e:
            logger.error(
                "notification_delivery_failed",
                extra={
                    "surface": surface,
                    "notification_id": notification.notification_id,
                    "error": str(e),
                },
            )
            return {"status": "error", "error": str(e)}

    async def _deliver_telegram(self, notification: Notification) -> dict:
        """Format and send notification via Telegram."""
        if not self._telegram_sender:
            return {"status": "skipped", "reason": "no_telegram_sender"}

        # Format message based on notification type
        if notification.type == "approval_request":
            approval_id = notification.data.get("approval_id", "")
            risk = notification.data.get("risk_level", "medium")
            text = f"*Approval Required* ({risk})\n\n*{notification.title}*\n{notification.body}"
            markup = json.dumps(
                {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Approve",
                                "callback_data": f"approve:{approval_id}",
                            },
                            {
                                "text": "Reject",
                                "callback_data": f"reject:{approval_id}",
                            },
                        ]
                    ]
                }
            )
            return await self._telegram_sender(
                text=text, parse_mode="Markdown", reply_markup=markup
            )
        elif notification.type == "critical_alert":
            text = f"*ALERT*\n\n*{notification.title}*\n{notification.body}"
            return await self._telegram_sender(text=text, parse_mode="Markdown")
        else:
            text = f"*{notification.title}*\n{notification.body}"
            return await self._telegram_sender(text=text, parse_mode="Markdown")

    async def _deliver_web(self, notification: Notification) -> dict:
        """Push notification to web dashboard via WebSocket/Redis pub/sub."""
        if not self._ws_sender:
            if self._redis:
                # Fallback: publish to Redis for WebSocket subscribers
                message = json.dumps(
                    {
                        "type": "notification",
                        "notification_id": notification.notification_id,
                        "notification_type": notification.type,
                        "title": notification.title,
                        "body": notification.body,
                        "data": notification.data,
                        "created_at": notification.created_at,
                    }
                )
                await self._redis.publish(f"jarvis:a2ui:{notification.user_id}", message)
                return {"status": "published"}
            return {"status": "skipped", "reason": "no_ws_sender"}

        return await self._ws_sender(notification)

    async def _mark_delivered(self, notification_id: str, surface: str) -> None:
        """Mark a notification as delivered on a surface (for dedup)."""
        if self._redis:
            key = f"jarvis:notif_delivered:{notification_id}"
            await self._redis.set(key, surface, ex=86400)  # 24h TTL
        self._delivered.setdefault(notification_id, set()).add(surface)

    async def is_delivered(self, notification_id: str) -> bool:
        """Check if a notification has already been delivered."""
        if self._redis:
            key = f"jarvis:notif_delivered:{notification_id}"
            return bool(await self._redis.exists(key))
        return notification_id in self._delivered
