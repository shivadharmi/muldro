"""Gmail Connector — fetch and normalize email events.

Responsibilities:
- Handle push notifications from Google Pub/Sub
- Accept test payloads for development
- Normalize emails into RawEvent format for the EventProcessor
- Extract actors (sender, recipients) for entity creation
"""

import logging
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.services.event_processor import EventProcessor, RawEvent

logger = logging.getLogger(__name__)


class GmailMessagePayload(BaseModel):
    """Normalized email shape — used directly in test mode,
    or produced by parsing a real Gmail API response."""

    message_id: str
    thread_id: str
    from_email: str
    from_name: str | None = None
    to: list[str] = []
    cc: list[str] | None = None
    subject: str = ""
    snippet: str = ""
    date: datetime | None = None
    labels: list[str] | None = None
    has_attachments: bool = False


class GmailConnector:
    """Fetch and process Gmail messages."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        event_processor: EventProcessor,
    ):
        self._settings = settings
        self._db = db
        self._event_processor = event_processor

    async def handle_push_notification(self, payload: dict, user_id: str) -> list[str]:
        """Process a Gmail push notification. Returns list of event_ids created."""
        # In production: decode Pub/Sub envelope, fetch messages via Gmail API
        # For now: accept a "messages" field with GmailMessagePayload dicts
        messages = payload.get("messages", [])
        if not messages:
            logger.debug("Gmail push with no messages field, skipping")
            return []

        event_ids = []
        for msg_data in messages:
            msg = GmailMessagePayload.model_validate(msg_data)
            raw = self._message_to_raw_event(msg, payload.get("account_id", "gmail_primary"))
            event_id = await self._event_processor.process(raw, user_id)
            if event_id:
                event_ids.append(event_id)

        logger.info("Gmail push: %d messages, %d events", len(messages), len(event_ids))
        return event_ids

    async def process_test_message(self, msg: GmailMessagePayload, user_id: str) -> str | None:
        """Process a single test message directly (bypasses Pub/Sub envelope)."""
        raw = self._message_to_raw_event(msg, "gmail_test")
        return await self._event_processor.process(raw, user_id)

    def _message_to_raw_event(self, msg: GmailMessagePayload, account_id: str) -> RawEvent:
        return RawEvent(
            source="gmail",
            source_account_id=account_id,
            event_type="email_received",
            entity_type="email_thread",
            entity_id=msg.thread_id,
            occurred_at=msg.date or datetime.now(timezone.utc),
            title=msg.subject,
            summary=msg.snippet,
            actor={
                "type": "person",
                "email": msg.from_email,
                "name": msg.from_name or msg.from_email,
            },
            raw_payload=msg.model_dump(mode="json"),
        )
