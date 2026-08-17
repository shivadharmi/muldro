"""Google Calendar connector — polls calendar events through the OpenConnector gateway.

Increment 2 retired this connector's native OAuth, so it no longer speaks Google
REST: it calls ``googlecalendar.list_events`` through the gateway and inherits
the envelope handling, pagination and cursor policy from :class:`GatewayConnector`.

**Why a timestamp cursor and not ``syncToken``.** ``syncToken`` IS in the action's
inputSchema, so a 1:1 port looked free — but the native connector detected an
expired token by reading ``resp.status_code == 410`` off the wire, and through
adapter -> OpenConnector -> Google this connector sees a result dict, not an HTTP
status. An opaque error cannot be told apart from "resync now", leaving only two
failure modes: stall forever, or full-resync every tick. A timestamp cannot expire.

**Deletions still arrive.** ``_normalize_event`` maps ``status: "cancelled"`` to
``event_cancelled``, and Google documents ``updatedMin`` as always including
entries deleted since that time *regardless of* ``showDeleted`` — so
``showDeleted`` is deliberately never sent (setting it would also change
initial-sync semantics).

**The initial/incremental asymmetry is preserved, not introduced.** An initial
sync answers "what is on my calendar" (``timeMin`` = now); an incremental sync
answers "what changed" (``updatedMin``). That is the same split the pre-gateway
connector had as ``timeMin`` vs ``syncToken``.
"""

import logging
from datetime import datetime, timedelta, timezone

from src.connectors.base import register_connector
from src.connectors.gateway_connector import OVERLAP_SECONDS, GatewayConnector
from src.connectors.poll_result import PollResult
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

# Defensive page cap for a single poll. A misbehaving provider that always
# returns a nextPageToken would otherwise loop forever. Truncation is reported
# structurally by PageWalk and consumed via _resolve_cursor, so a capped walk
# holds its cursor instead of skipping the undrained remainder.
MAX_PAGES = 5

# Events requested per page. The action's inputSchema allows up to 2500; a
# modest page keeps a single gateway call well inside the poll budget.
PAGE_SIZE = 50


def _rfc3339(when: datetime) -> str:
    """Render a UTC datetime in the RFC 3339 form the action's schema declares."""
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@register_connector("calendar")
class CalendarConnector(GatewayConnector):
    """Polls Google Calendar through the gateway, watermarked on ``updated``."""

    cursor_type: str = "timestamp"

    READ_ACTION = "googlecalendar.list_calendars"
    LIST_ACTION = "googlecalendar.list_events"

    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Poll Calendar for events changed since the cursor watermark.

        ``credentials`` is unused: the credential lives in OpenConnector and the
        gateway injects it per connection. The parameter stays because
        ``BaseConnector.poll`` defines it.
        """
        watermark = self._sane_rfc3339_cursor(cursor)

        payload: dict = {
            "calendarId": "primary",
            "singleEvents": True,
            "maxResults": PAGE_SIZE,
        }
        if watermark is None:
            # Initial sync: what is on my calendar from now on.
            payload["timeMin"] = _rfc3339(datetime.now(timezone.utc))
        else:
            # Incremental: what changed, re-reading OVERLAP_SECONDS of the
            # previous window as insurance against clock skew and
            # second-granularity boundaries. Duplicates are absorbed by
            # EventProcessor's idempotency key, so the only cost is quota.
            payload["updatedMin"] = _rfc3339(watermark - timedelta(seconds=OVERLAP_SECONDS))

        walk = await self._walk_pages(
            self.LIST_ACTION, payload, items_key="items", max_pages=MAX_PAGES
        )
        if walk.error_class is not None:
            return PollResult(events=[], cursor=cursor, error_class=walk.error_class)

        events: list[RawEvent] = []
        observed: str | None = None
        for page in walk.pages:
            for item in page:
                event = self._normalize_event(item, user_id)
                if event:
                    events.append(event)
                # Google renders `updated` as a UTC RFC 3339 stamp with a fixed
                # "Z" suffix and millisecond precision, so a lexicographic max is
                # a chronological max and the winner round-trips straight back as
                # the cursor. (Were the precision ever to vary within one second,
                # "…:00Z" would sort above "…:00.500Z" — landing up to a second
                # EARLY, i.e. an extra re-read. The error can only under-advance.)
                # `updated` is optional in the recorded outputSchema (only id and
                # status are required), and an absent or empty stamp is not a
                # watermark: taking it would make the next poll reject the cursor
                # and restart from now(), skipping the window in between.
                updated = item.get("updated")
                if (
                    isinstance(updated, str)
                    and updated
                    and (observed is None or updated > observed)
                ):
                    observed = updated

        # Never advance past an undrained window: a truncated walk, or a walk
        # with no watermark to advance TO, holds the incoming cursor.
        new_cursor = self._resolve_cursor(walk, incoming=cursor, observed=observed)

        logger.info("Calendar poll: %d events", len(events))
        return PollResult(events=events, cursor=new_cursor)

    @staticmethod
    def _normalize_event(item: dict, user_id: str) -> RawEvent | None:
        """Convert a Google Calendar event to a RawEvent."""
        status = item.get("status", "confirmed")
        event_type_map = {
            "confirmed": "event_created",
            "tentative": "event_created",
            "cancelled": "event_cancelled",
        }

        start = item.get("start", {})
        start_time = start.get("dateTime") or start.get("date", "")
        end = item.get("end", {})
        end_time = end.get("dateTime") or end.get("date", "")

        organizer = item.get("organizer", {})
        attendees = item.get("attendees", [])
        summary = item.get("summary", "(no title)")

        attendee_names = [a.get("displayName") or a.get("email", "") for a in attendees[:5]]
        description = f"{summary} from {start_time} to {end_time}"
        if attendee_names:
            description += f" with {', '.join(attendee_names)}"

        occurred_at = None
        if start_time:
            try:
                occurred_at = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                # All-day events expose start.date ("2026-06-25") with no time or
                # offset, so fromisoformat yields a NAIVE datetime, whereas timed
                # events (start.dateTime with offset) yield tz-aware. Normalize the
                # naive date-midnight to UTC-aware so occurred_at is uniformly aware
                # and downstream comparisons never raise on mixed naive/aware values.
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        return RawEvent(
            source="calendar",
            source_account_id="calendar_primary",
            event_type=event_type_map.get(status, "event_updated"),
            entity_type="meeting",
            entity_id=item.get("id", ""),
            occurred_at=occurred_at,
            title=summary,
            summary=description,
            actor={
                "type": "person",
                "email": organizer.get("email", ""),
                "name": organizer.get("displayName", ""),
            },
            raw_payload={
                "calendar_event_id": item.get("id"),
                "status": status,
                "attendee_count": len(attendees),
            },
        )
