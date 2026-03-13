"""WhatsApp Connector — ingest and normalize WhatsApp messages.

Responsibilities:
- Handle WhatsApp Business API webhook callbacks
- Accept test payloads for development
- Normalize messages into RawEvent format for the EventProcessor
- Extract actors (sender) for entity creation

NOTE: This is a stub connector for v1. Full WhatsApp Business API
integration requires Meta Business verification and phone number setup.
"""

import logging
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.services.event_processor import EventProcessor, RawEvent

logger = logging.getLogger(__name__)


class WhatsAppMessagePayload(BaseModel):
    """Normalized WhatsApp message shape."""

    message_id: str
    from_number: str
    from_name: str | None = None
    text: str = ""
    message_type: str = "text"  # text, image, document, etc.
    timestamp: datetime | None = None
    context_message_id: str | None = None  # reply-to message ID


class WhatsAppConnector:
    """Process WhatsApp messages into normalized events."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        event_processor: EventProcessor,
    ):
        self._settings = settings
        self._db = db
        self._event_processor = event_processor

    async def handle_webhook(self, payload: dict, user_id: str) -> list[str]:
        """Process a WhatsApp Business API webhook callback.

        Expects the standard Meta webhook format with entry[].changes[].value.messages[].
        """
        event_ids = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                raw_contacts = value.get("contacts", [])
                contacts = {c["wa_id"]: c.get("profile", {}).get("name") for c in raw_contacts}

                for msg_data in messages:
                    msg = WhatsAppMessagePayload(
                        message_id=msg_data.get("id", ""),
                        from_number=msg_data.get("from", ""),
                        from_name=contacts.get(msg_data.get("from")),
                        text=msg_data.get("text", {}).get("body", ""),
                        message_type=msg_data.get("type", "text"),
                        context_message_id=msg_data.get("context", {}).get("id"),
                    )

                    raw = self._message_to_raw_event(msg, "whatsapp_default")
                    event_id = await self._event_processor.process(raw, user_id)
                    if event_id:
                        event_ids.append(event_id)

        if event_ids:
            logger.info("WhatsApp: processed %d messages", len(event_ids))
        return event_ids

    async def process_test_message(self, msg: WhatsAppMessagePayload, user_id: str) -> str | None:
        """Process a single test message directly."""
        raw = self._message_to_raw_event(msg, "whatsapp_test")
        return await self._event_processor.process(raw, user_id)

    def _message_to_raw_event(self, msg: WhatsAppMessagePayload, account_id: str) -> RawEvent:
        title = f"WhatsApp message from {msg.from_name or msg.from_number}"
        if msg.context_message_id:
            title = f"WhatsApp reply from {msg.from_name or msg.from_number}"

        return RawEvent(
            source="whatsapp",
            source_account_id=account_id,
            event_type="whatsapp_message",
            entity_type="whatsapp_chat",
            entity_id=f"wa:{msg.from_number}",
            occurred_at=msg.timestamp or datetime.now(timezone.utc),
            title=title,
            summary=msg.text[:500] if msg.text else "",
            actor={
                "type": "person",
                "phone": msg.from_number,
                "name": msg.from_name or msg.from_number,
            },
            raw_payload={
                "message_id": msg.message_id,
                "message_type": msg.message_type,
                "context_message_id": msg.context_message_id,
            },
        )
