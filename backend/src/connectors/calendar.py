"""Calendar Connector — fetch and normalize calendar events.

Responsibilities:
- OAuth token management
- Poll for upcoming events and changes
- Normalize calendar events into NormalizedEvent format
- Extract attendees for entity creation
- Detect meeting conflicts and risks
"""


class CalendarConnector:
    """Fetch and process Google Calendar events."""

    async def handle_push_notification(self, payload: dict, user_id: str) -> list[str]:
        """Process a Calendar push notification. Returns event_ids."""
        # TODO: Implement
        return []

    async def sync(self, user_id: str, account_id: str) -> list[str]:
        """Full sync — poll for calendar changes. Returns event_ids."""
        # TODO: Implement
        return []
