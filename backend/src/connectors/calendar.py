"""Calendar Connector — fetch and normalize calendar events.

Responsibilities:
- Handle push notifications (test payload mode + webhook structure)
- Accept test payloads for development
- Normalize calendar events into RawEvent format for the EventProcessor
- Extract attendees for entity creation
- Detect meeting conflicts and risks
"""

import logging
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.services.event_processor import EventProcessor, RawEvent

logger = logging.getLogger(__name__)


class CalendarAttendee(BaseModel):
    """Single attendee on a calendar event."""

    email: str
    name: str | None = None
    response_status: str = "needsAction"  # accepted, declined, tentative, needsAction
    organizer: bool = False


class CalendarEventPayload(BaseModel):
    """Normalized calendar event shape — used directly in test mode,
    or produced by parsing a real Google Calendar API response."""

    calendar_event_id: str
    calendar_id: str = "primary"
    title: str = ""
    description: str | None = None
    location: str | None = None
    start_time: datetime
    end_time: datetime
    attendees: list[CalendarAttendee] = []
    organizer_email: str | None = None
    recurrence: str | None = None
    status: str = "confirmed"  # confirmed, tentative, cancelled
    html_link: str | None = None
    conference_link: str | None = None
    updated_at: datetime | None = None


class CalendarConnector:
    """Fetch and process Google Calendar events."""

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
        """Process a Calendar push notification. Returns event_ids."""
        events = payload.get("events", [])
        if not events:
            logger.debug("Calendar push with no events field, skipping")
            return []

        event_ids = []
        for evt_data in events:
            evt = CalendarEventPayload.model_validate(evt_data)
            raw = self._event_to_raw_event(evt, payload.get("account_id", "calendar_primary"))
            event_id = await self._event_processor.process(raw, user_id)
            if event_id:
                event_ids.append(event_id)

        logger.info(
            "Calendar push: %d events, %d processed",
            len(events),
            len(event_ids),
        )
        return event_ids

    async def process_test_event(self, evt: CalendarEventPayload, user_id: str) -> str | None:
        """Process a single test calendar event directly."""
        raw = self._event_to_raw_event(evt, "calendar_test")
        return await self._event_processor.process(raw, user_id)

    def _event_to_raw_event(self, evt: CalendarEventPayload, account_id: str) -> RawEvent:
        attendee_names = [a.name or a.email for a in evt.attendees if not a.organizer]
        summary_parts = []
        if evt.description:
            summary_parts.append(evt.description[:200])
        if attendee_names:
            summary_parts.append(f"Attendees: {', '.join(attendee_names)}")
        if evt.location:
            summary_parts.append(f"Location: {evt.location}")
        if evt.conference_link:
            summary_parts.append(f"Conference: {evt.conference_link}")

        organizer = None
        if evt.organizer_email:
            organizer = {
                "type": "person",
                "email": evt.organizer_email,
                "name": evt.organizer_email,
            }
            for a in evt.attendees:
                if a.organizer and a.name:
                    organizer["name"] = a.name
                    break

        return RawEvent(
            source="calendar",
            source_account_id=account_id,
            event_type="calendar_event_created",
            entity_type="calendar_event",
            entity_id=evt.calendar_event_id,
            occurred_at=evt.start_time,
            title=evt.title,
            summary=" | ".join(summary_parts) if summary_parts else None,
            actor=organizer,
            raw_payload=evt.model_dump(mode="json"),
        )
