"""Google Calendar connector — polls for calendar events."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)


@register_connector("calendar")
class CalendarConnector(BaseConnector):
    """Polls Google Calendar API using syncToken for incremental fetch."""

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll Calendar for event changes since syncToken cursor."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return [], cursor

        events = []
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                params = {"singleEvents": "true", "maxResults": 50}
                if cursor:
                    params["syncToken"] = cursor
                else:
                    # Initial sync: get events from now to 7 days out
                    now = datetime.now(timezone.utc).isoformat()
                    params["timeMin"] = now

                resp = await client.get(
                    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15,
                )

                if resp.status_code == 410:
                    # Sync token expired, full sync
                    return await self.poll(user_id, None, credentials)

                if resp.status_code == 200:
                    data = resp.json()
                    new_cursor = data.get("nextSyncToken", cursor)

                    for item in data.get("items", []):
                        event = self._normalize_event(item, user_id)
                        if event:
                            events.append(event)

        except Exception:
            logger.warning("Calendar poll failed for user %s", user_id, exc_info=True)

        logger.info("Calendar poll: %d events", len(events))
        return events, new_cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        """Test Calendar connection."""
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.googleapis.com/calendar/v3/calendars/primary",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                status = "healthy" if resp.status_code == 200 else "down"
                return ConnectorHealth(
                    provider="calendar",
                    status=status,
                    last_poll_at=datetime.now(timezone.utc) if status == "healthy" else None,
                    error=None if status == "healthy" else f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(
                provider="calendar", status="down", last_poll_at=None, error=str(e)
            )

    supports_actions: bool = True
    available_actions: list[str] = ["create_event", "update_event"]

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        """Execute a Calendar write action."""
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "create_event": self._action_create_event,
            "update_event": self._action_update_event,
        }
        return await dispatch[action](params, access_token)

    async def _action_create_event(self, params: dict, access_token: str) -> dict:
        """Create a calendar event."""
        import httpx

        body = {
            "summary": params.get("summary", ""),
            "start": params.get("start", {}),
            "end": params.get("end", {}),
        }
        if params.get("description"):
            body["description"] = params["description"]
        if params.get("location"):
            body["location"] = params["location"]
        if params.get("attendees"):
            body["attendees"] = [{"email": e} for e in params["attendees"]]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "status": "ok",
                    "event_id": data.get("id"),
                    "html_link": data.get("htmlLink"),
                }
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_update_event(self, params: dict, access_token: str) -> dict:
        """Update a calendar event."""
        import httpx

        event_id = params.get("event_id", "")
        if not event_id:
            return {"status": "error", "error": "event_id required"}

        body = {}
        for key in ("summary", "description", "location", "start", "end"):
            if params.get(key):
                body[key] = params[key]
        if params.get("attendees"):
            body["attendees"] = [{"email": e} for e in params["attendees"]]

        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"status": "ok", "event_id": data.get("id")}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/google/authorize"

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
