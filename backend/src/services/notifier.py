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
    "telegram": 5,  # per hour
    "web": 15,
    "slack": 8,
    "email": 3,
}


class Notifier:
    """Coordinates notification delivery across surfaces with persistence."""

    def __init__(
        self,
        surface_registry: SurfaceRegistry,
        redis=None,
        telegram_sender=None,
        websocket_sender=None,
        db=None,
    ):
        self._registry = surface_registry
        self._redis = redis
        self._telegram_sender = telegram_sender
        self._ws_sender = websocket_sender
        self._db = db
        # Track delivered notifications for dedup
        self._delivered: dict[str, set[str]] = {}

    async def _check_rate_limit(self, user_id: str, surface: str) -> bool:
        """Check if a notification can be sent to this surface within rate limits.

        Uses Redis INCR with 1-hour TTL. Returns True if under limit.
        """
        if not self._redis:
            return True
        key = f"notifier:rate:{user_id}:{surface}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, 3600)
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

        # Emit notification.sent domain event
        if self._redis:
            try:
                import json as _json

                await self._redis.publish(
                    f"jarvis:realtime:{user_id}",
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

                sync_key = f"jarvis:pending_sync:{user_id}"
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
                result = await self._deliver_telegram(notification)
            elif surface == "web":
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
                        f"jarvis:realtime:{notification.user_id}",
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
                    "channel": notification.data.get("slack_channel", "#jarvis"),
                },
                user_id=notification.user_id,
                workspace_id=workspace_id,
            )
            return {"status": "sent", "slack_result": result}
        except Exception as e:
            logger.warning("Slack delivery failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}

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
            return {"status": "error", "error": str(e)}

    async def _deliver_web(self, notification: Notification) -> dict:
        """Push notification to web dashboard via WebSocket/Redis pub/sub."""
        if not self._ws_sender:
            if self._redis:
                channel = f"jarvis:a2ui:{notification.user_id}"

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

                # Also push a typed surface for approval notifications so
                # they appear on the workspace dashboard in real time.
                if notification.type == "approval_request":
                    try:
                        from datetime import datetime, timezone

                        from src.orchestrator.contracts import WorkspaceSurfacePush
                        from src.ui.contracts import SurfaceMetric, SurfacePreview
                        from src.ui.renderer import build_detail_config

                        risk_level = (notification.data or {}).get("risk_level", "medium")
                        risk_variant = (
                            "warning" if risk_level in ("high", "critical") else "default"
                        )

                        # Use approval_{approval_id} format so the surface
                        # detail endpoint can resolve the tab builders via
                        # the "approval_" prefix in _PREFIX_MAP.  This also
                        # deduplicates with the REST-polled surfaces built by
                        # SurfaceService._build_approval_surfaces().
                        approval_id = (notification.data or {}).get("approval_id", "")
                        surface_id = (
                            f"approval_{approval_id}" if approval_id else f"notif_surf_{ULID()}"
                        )
                        preview = SurfacePreview(
                            title=notification.title,
                            subtitle=(notification.body or "")[:120] or None,
                            status="awaiting_approval",
                            priority="high" if risk_level in ("high", "critical") else "medium",
                            metrics=[
                                SurfaceMetric(label="Risk", value=risk_level, variant=risk_variant),
                            ],
                        )
                        detail_config = build_detail_config("approval", surface_id)

                        surface = WorkspaceSurfacePush(
                            id=surface_id,
                            kind="approval",
                            preview=preview.model_dump(mode="json"),
                            detail_config=(
                                detail_config.model_dump(mode="json") if detail_config else None
                            ),
                            decision="approval_requested",
                            created_at=datetime.now(timezone.utc).isoformat(),
                        )
                        ws_msg = json.dumps(
                            {"type": "surface", "surface": surface.model_dump(mode="json")}
                        )
                        await self._redis.publish(channel, ws_msg)
                    except Exception:
                        logger.debug(
                            "Failed to push approval surface for %s",
                            notification.notification_id,
                            exc_info=True,
                        )

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
