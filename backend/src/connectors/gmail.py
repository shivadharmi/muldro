"""Gmail Connector — fetch and normalize email events.

Responsibilities:
- OAuth token management (stored encrypted in connector_accounts)
- Handle push notifications from Google Pub/Sub
- Poll for new messages when push is unavailable
- Normalize emails into NormalizedEvent format
- Extract actors (sender, recipients) for entity creation
"""


class GmailConnector:
    """Fetch and process Gmail messages."""

    async def handle_push_notification(self, payload: dict, user_id: str) -> list[str]:
        """Process a Gmail push notification. Returns list of event_ids created."""
        # TODO: Implement
        # 1. Decode Pub/Sub message
        # 2. Fetch new messages since last sync cursor
        # 3. For each message: normalize, extract entities, create event
        # 4. Update sync cursor
        return []

    async def sync(self, user_id: str, account_id: str) -> list[str]:
        """Full sync — poll for new messages. Returns event_ids."""
        # TODO: Implement
        return []
