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

from src.errors import classify
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


def compute_priority_score(
    urgency: float = 0.5,
    goal_relevance: float = 0.5,
    novelty: float = 0.5,
    confidence: float = 0.5,
    interruptibility: float = 0.5,
) -> float:
    """Compute notification priority score using weighted formula."""
    return (
        0.30 * urgency
        + 0.25 * goal_relevance
        + 0.20 * novelty
        + 0.15 * confidence
        + 0.10 * interruptibility
    )


SURFACE_RATE_LIMITS: dict[str, int] = {
    "web": 15,  # per hour
    "slack": 8,
    "email": 3,
}


class Notifier:
    """Coordinates notification delivery across surfaces with persistence."""

    def __init__(
        self,
        surface_registry: SurfaceRegistry,
        redis=None,
        websocket_sender=None,
        db=None,
    ):
        self._registry = surface_registry
        self._redis = redis
        self._ws_sender = websocket_sender
        self._db = db
        # Track delivered notifications for dedup
        self._delivered: dict[str, set[str]] = {}

    async def _hold_for_briefing(self, notification: Notification, priority: float) -> None:
        """Store a notification as a briefing item instead of delivering it."""
        if self._redis:
            try:
                key = f"notifier:briefing_hold:{notification.user_id}"
                entry = json.dumps(
                    {
                        "notification_id": notification.notification_id,
                        "title": notification.title,
                        "body": notification.body,
                        "type": notification.type,
                        "priority": priority,
                        "created_at": notification.created_at,
                    }
                )
                await self._redis.lpush(key, entry)
                await self._redis.expire(key, 86400)  # 24h TTL
            except Exception:
                logger.debug("Failed to hold notification for briefing", exc_info=True)

    async def _check_rate_limit(self, user_id: str, surface: str) -> bool:
        """Check if a notification can be sent to this surface within rate limits.

        Uses a Redis pipeline to atomically INCR the counter and always refresh
        the TTL, ensuring the window expires correctly even under concurrent writes.
        """
        if not self._redis:
            return True
        key = f"notifier:rate:{user_id}:{surface}"
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, 3600)
        results = await pipe.execute()
        count = results[0]
        return count <= SURFACE_RATE_LIMITS.get(surface, 10)

    async def notify(
        self,
        user_id: str,
        notification_type: str,
        title: str,
        body: str,
        data: dict | None = None,
        workspace_id: str = "",
    ) -> dict:
        """Send a notification to the appropriate surface(s).

        Returns delivery status per surface.
        """
        priority = compute_priority_score(
            urgency=data.get("urgency", 0.5) if data else 0.5,
            goal_relevance=data.get("goal_relevance", 0.5) if data else 0.5,
            novelty=data.get("novelty", 0.5) if data else 0.5,
            confidence=data.get("confidence", 0.5) if data else 0.5,
            interruptibility=data.get("interruptibility", 0.5) if data else 0.5,
        )

        # Resolve workspace_id from user_id if not provided
        if not workspace_id and self._db and user_id:
            try:
                from src.services.workspace_resolver import resolve_workspace_id

                workspace_id = await resolve_workspace_id(self._db, user_id)
            except Exception:
                logger.debug("Could not resolve workspace_id for user %s", user_id)

        payload = dict(data or {})
        if workspace_id and "workspace_id" not in payload:
            payload["workspace_id"] = workspace_id

        notification = Notification(
            notification_id=f"notif_{ULID()}",
            user_id=user_id,
            type=notification_type,
            title=title,
            body=body,
            data=payload,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Validate workspace membership
        if workspace_id and self._db:
            try:
                from sqlalchemy import select

                from src.models.users import WorkspaceMember

                result = await self._db.execute(
                    select(WorkspaceMember).where(
                        WorkspaceMember.user_id == user_id,
                        WorkspaceMember.workspace_id == workspace_id,
                    )
                )
                if not result.scalar_one_or_none():
                    logger.warning(
                        "Notification blocked: user %s not in workspace %s",
                        user_id,
                        workspace_id,
                    )
                    return {"status": "blocked", "reason": "workspace_membership"}
            except Exception:
                logger.debug("Workspace validation skipped", exc_info=True)

        # Persist to DB if available
        if self._db:
            try:
                from src.models.notifications import Notification as NotifModel

                notif_record = NotifModel(
                    notification_id=notification.notification_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    channel=notification_type,
                    title=title,
                    body=body,
                    payload_json=payload,
                    priority_score=priority,
                    status="pending",
                )
                self._db.add(notif_record)
                await self._db.flush()
            except Exception:
                logger.warning("Failed to persist notification", exc_info=True)

        # Types that bypass priority/rate-limit filters
        _bypass_filter = ("approval_request", "critical_alert", "auto_execute_notify")
        # Types that deliver to ALL surfaces (not just preferred)
        _broadcast_types = ("approval_request", "critical_alert")
        if notification_type not in _bypass_filter:
            if priority < 0.3:
                logger.info(
                    "notification_silent",
                    extra={
                        "notification_id": notification.notification_id,
                        "priority": priority,
                    },
                )
                return {"status": "silent", "priority": priority}
            if priority < 0.6:
                await self._hold_for_briefing(notification, priority)
                return {"status": "held_for_briefing", "priority": priority}

        surfaces = await self._registry.get_active_surfaces(user_id)
        if not surfaces:
            # No active surfaces — queue for later delivery
            logger.warning(
                "no_active_surfaces",
                extra={"user_id": user_id, "notification_id": notification.notification_id},
            )
            return {"status": "queued", "surfaces": []}

        # Rate-limit filtering: remove surfaces that are over their hourly cap
        if notification_type not in _bypass_filter:
            allowed_surfaces = []
            for surface in surfaces:
                if await self._check_rate_limit(user_id, surface):
                    allowed_surfaces.append(surface)
            if not allowed_surfaces:
                await self._hold_for_briefing(notification, priority)
                return {"status": "rate_limited", "priority": priority}
            surfaces = allowed_surfaces

        results = {}

        if notification_type in _broadcast_types:
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

        # Emit notification.sent domain event
        if self._redis:
            try:
                import json as _json

                await self._redis.publish(
                    f"muldro:realtime:{user_id}",
                    _json.dumps(
                        {
                            "event": "notification.sent",
                            "notification_id": notification.notification_id,
                            "type": notification_type,
                            "title": title,
                        }
                    ),
                )
            except Exception:
                logger.debug("Failed to emit notification.sent event", exc_info=True)

        # Store sync event for polling fallback (reconnection safety)
        if self._redis and workspace_id:
            try:
                import json as _json

                sync_key = f"muldro:pending_sync:{user_id}"
                await self._redis.lpush(
                    sync_key,
                    _json.dumps(
                        {
                            "action": notification_type,
                            "notification_id": notification.notification_id,
                        }
                    ),
                )
                await self._redis.expire(sync_key, 300)
            except Exception:
                logger.debug("Surface sync fallback failed", exc_info=True)

        return {"status": "sent", "surfaces": results}

    async def on_action_taken(
        self,
        user_id: str,
        notification_id: str,
        surface: str,
    ) -> None:
        """When user acts on one surface, notify others to update.

        E.g., user approves on Slack -> web dashboard updates status.
        """
        if self._redis:
            message = json.dumps(
                {
                    "type": "notification_resolved",
                    "notification_id": notification_id,
                    "resolved_on": surface,
                }
            )
            await self._redis.publish(f"muldro:surface_sync:{user_id}", message)

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
            if surface == "web":
                result = await self._deliver_web(notification)
            elif surface == "slack":
                result = await self._deliver_slack(notification)
            elif surface == "email":
                result = await self._deliver_email(notification)
            else:
                return {"status": "unsupported_surface"}

            # Emit notification.delivered domain event
            if result.get("status") not in ("error", "skipped") and self._redis:
                try:
                    await self._redis.publish(
                        f"muldro:realtime:{notification.user_id}",
                        json.dumps(
                            {
                                "event": "notification.delivered",
                                "notification_id": notification.notification_id,
                                "surface": surface,
                            }
                        ),
                    )
                except Exception:
                    logger.debug("Failed to emit notification.delivered", exc_info=True)

            return result
        except Exception as e:
            logger.error(
                "notification_delivery_failed",
                extra={
                    "surface": surface,
                    "notification_id": notification.notification_id,
                    "error": str(e),
                },
            )
            # The returned dict may be relayed to a surface — expose only the
            # safe message + code, never the raw exception. Logs keep str(e).
            code, message, _ = classify(e)
            return {"status": "error", "error": message, "error_code": code}

    async def _deliver_slack(self, notification: Notification) -> dict:
        """Send notification via Slack using MCP bridge."""
        try:
            from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

            tool_name = "slack_send_message"
            if not is_mcp_tool(tool_name):
                return {"status": "skipped", "reason": "slack_mcp_not_available"}

            text = f"*{notification.title}*\n{notification.body or ''}"
            workspace_id = str(notification.data.get("workspace_id", "") or "")
            result = await call_mcp_tool(
                tool_name,
                {
                    "text": text,
                    "channel": notification.data.get("slack_channel", "#muldro"),
                },
                user_id=notification.user_id,
                workspace_id=workspace_id,
            )
            return {"status": "sent", "slack_result": result}
        except Exception as e:
            logger.warning("Slack delivery failed: %s", e, exc_info=True)
            code, message, _ = classify(e)
            return {"status": "error", "error": message, "error_code": code}

    async def _deliver_email(self, notification: Notification) -> dict:
        """Send notification via email using MCP bridge or SES fallback."""
        try:
            from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

            if is_mcp_tool("email_send"):
                workspace_id = str(notification.data.get("workspace_id", "") or "")
                result = await call_mcp_tool(
                    "email_send",
                    {
                        "to": notification.data.get("email", ""),
                        "subject": notification.title,
                        "body": notification.body,
                    },
                    user_id=notification.user_id,
                    workspace_id=workspace_id,
                )
                return {"status": "sent", "via": "mcp", "result": result}
        except Exception:
            logger.debug("MCP email_send unavailable", exc_info=True)

        # SES fallback via EmailSender
        try:
            from src.config.settings import get_settings
            from src.services.email_sender import EmailSender

            to_addr = notification.data.get("email", "")
            if not to_addr:
                return {"status": "skipped", "reason": "no_email_address"}

            sender = EmailSender(get_settings())
            await sender.send(
                to=to_addr,
                subject=notification.title,
                body_text=notification.body,
            )
            return {"status": "sent", "via": "ses"}
        except Exception as e:
            logger.warning("Email (SES) delivery failed: %s", e, exc_info=True)
            code, message, _ = classify(e)
            return {"status": "error", "error": message, "error_code": code}

    async def _deliver_web(self, notification: Notification) -> dict:
        """Push notification to web dashboard via WebSocket/Redis pub/sub."""
        if not self._ws_sender:
            if self._redis:
                channel = f"muldro:a2ui:{notification.user_id}"

                # Publish notification message
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
                await self._redis.publish(channel, message)

                # Note: approval notifications no longer emit a standalone
                # workspace surface card. The run's unified surface (emitted
                # by GraphExecutor with phase=approval_needed) already carries
                # the inline approval block via units.approval_card.

                return {"status": "published"}
            return {"status": "skipped", "reason": "no_ws_sender"}

        return await self._ws_sender(notification)

    async def _mark_delivered(self, notification_id: str, surface: str) -> None:
        """Mark a notification as delivered on a surface (for dedup)."""
        if self._redis:
            key = f"muldro:notif_delivered:{notification_id}"
            await self._redis.set(key, surface, ex=86400)  # 24h TTL
        # Evict oldest entries when in-memory cache exceeds limit
        if len(self._delivered) >= 10_000:
            keys_to_remove = list(self._delivered.keys())[:1000]
            for k in keys_to_remove:
                del self._delivered[k]
        self._delivered.setdefault(notification_id, set()).add(surface)

    async def is_delivered(self, notification_id: str) -> bool:
        """Check if a notification has already been delivered."""
        if self._redis:
            key = f"muldro:notif_delivered:{notification_id}"
            return bool(await self._redis.exists(key))
        return notification_id in self._delivered
