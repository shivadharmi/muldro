"""Slack Connector — ingest and normalize Slack events.

Responsibilities:
- Handle Slack Events API webhooks (url_verification + event_callback)
- Accept test payloads for development
- Normalize messages into RawEvent format for the EventProcessor
- Extract actors (sender) for entity creation
"""

import logging
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.services.event_processor import EventProcessor, RawEvent

logger = logging.getLogger(__name__)


class SlackMessagePayload(BaseModel):
    """Normalized Slack message shape."""

    channel_id: str
    channel_name: str | None = None
    message_ts: str  # Slack timestamp (also serves as message ID)
    thread_ts: str | None = None  # Thread parent timestamp
    user_id: str | None = None
    user_name: str | None = None
    user_email: str | None = None
    text: str = ""
    message_type: str = "message"  # message, reaction_added, channel_join, etc.
    workspace_id: str = ""
    timestamp: datetime | None = None


class SlackConnector:
    """Process Slack events into normalized events."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        event_processor: EventProcessor,
    ):
        self._settings = settings
        self._db = db
        self._event_processor = event_processor

    async def handle_event_callback(self, payload: dict, user_id: str) -> list[str]:
        """Process a Slack Events API callback. Returns list of event_ids created.

        Handles the event_callback type from Slack's Events API.
        """
        event = payload.get("event", {})
        if not event:
            logger.debug("Slack callback with no event field, skipping")
            return []

        event_type = event.get("type", "")
        if event_type != "message" or event.get("subtype") == "bot_message":
            logger.debug("Skipping Slack event type=%s", event_type)
            return []

        msg = SlackMessagePayload(
            channel_id=event.get("channel", ""),
            message_ts=event.get("ts", ""),
            thread_ts=event.get("thread_ts"),
            user_id=event.get("user"),
            text=event.get("text", ""),
            workspace_id=payload.get("team_id", ""),
        )

        raw = self._message_to_raw_event(msg, payload.get("team_id", "slack_default"))
        event_id = await self._event_processor.process(raw, user_id)

        if event_id:
            logger.info("Slack event processed: %s", event_id)
            return [event_id]
        return []

    async def process_test_message(self, msg: SlackMessagePayload, user_id: str) -> str | None:
        """Process a single test message directly (bypasses Slack API envelope)."""
        raw = self._message_to_raw_event(msg, "slack_test")
        return await self._event_processor.process(raw, user_id)

    def _message_to_raw_event(self, msg: SlackMessagePayload, account_id: str) -> RawEvent:
        thread_id = msg.thread_ts or msg.message_ts
        summary = msg.text[:500] if msg.text else ""

        channel_label = msg.channel_name or msg.channel_id
        title = f"Slack message in #{channel_label}"
        if msg.thread_ts:
            title = f"Slack thread reply in #{channel_label}"

        return RawEvent(
            source="slack",
            source_account_id=account_id,
            event_type="slack_message",
            entity_type="slack_thread",
            entity_id=f"{msg.channel_id}:{thread_id}",
            occurred_at=msg.timestamp or datetime.now(timezone.utc),
            title=title,
            summary=summary,
            actor={
                "type": "person",
                "slack_user_id": msg.user_id or "unknown",
                "name": msg.user_name or msg.user_id or "unknown",
                "email": msg.user_email,
            },
            raw_payload={
                "channel_id": msg.channel_id,
                "channel_name": msg.channel_name,
                "message_ts": msg.message_ts,
                "thread_ts": msg.thread_ts,
                "workspace_id": msg.workspace_id,
            },
        )
