"""Notification Service — outbound notifications to user channels.

Responsibilities:
- Send notifications to configured channels (Slack, WhatsApp, etc.)
- Format messages for each channel
- Track delivery status
- Respect user preferences for notification frequency and channels
"""

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class NotificationService:
    """Send outbound notifications to user channels."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db

    async def notify(
        self,
        user_id: str,
        title: str,
        body: str,
        channel: str = "slack",
        urgency: str = "normal",
        metadata: dict | None = None,
    ) -> dict:
        """Send a notification. Returns delivery status."""
        if channel == "slack":
            return await self._send_slack(title, body, urgency)

        logger.warning("Unsupported notification channel: %s", channel)
        return {"delivered": False, "channel": channel, "error": "unsupported_channel"}

    async def notify_approval_needed(
        self, user_id: str, approval_id: str, title: str, risk_level: str
    ) -> dict:
        """Send approval notification to configured channels."""
        emoji = "🔴" if risk_level == "high" else "🟡" if risk_level == "medium" else "🟢"
        body = (
            f"{emoji} *Approval needed*: {title}\n"
            f"Risk: {risk_level} | ID: `{approval_id}`\n"
            f"Reply with approve/reject in Jarvis."
        )
        return await self.notify(user_id, title=f"Approval: {title}", body=body, urgency="high")

    async def notify_execution_complete(self, user_id: str, plan_goal: str, status: str) -> dict:
        """Notify user that an execution completed."""
        emoji = "✅" if status == "completed" else "❌"
        body = f"{emoji} Task *{status}*: {plan_goal}"
        return await self.notify(user_id, title=f"Task {status}", body=body, urgency="normal")

    async def notify_briefing_ready(self, user_id: str, headline: str) -> dict:
        """Notify user that the daily briefing is ready."""
        body = f"📋 *Your daily briefing is ready*\n{headline}\nOpen Jarvis to see details."
        return await self.notify(user_id, title="Daily Briefing", body=body, urgency="low")

    async def _send_slack(self, title: str, body: str, urgency: str) -> dict:
        """Send a message to Slack via incoming webhook."""
        webhook_url = self._settings.slack_webhook_url
        if not webhook_url:
            logger.debug("No Slack webhook URL configured, skipping notification")
            return {"delivered": False, "channel": "slack", "error": "not_configured"}

        payload = {"text": body}

        # Use blocks for richer formatting if urgent
        if urgency == "high":
            payload["blocks"] = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": body},
                }
            ]

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(webhook_url, json=payload, timeout=10)
                if resp.status_code == 200:
                    logger.info("Slack notification sent: %s", title)
                    return {
                        "delivered": True,
                        "channel": "slack",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                else:
                    logger.warning("Slack webhook returned %d", resp.status_code)
                    return {
                        "delivered": False,
                        "channel": "slack",
                        "error": f"status_{resp.status_code}",
                    }
        except Exception:
            logger.warning("Slack notification failed", exc_info=True)
            return {"delivered": False, "channel": "slack", "error": "request_failed"}
