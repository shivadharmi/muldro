"""Gmail connector — polls Gmail API for new messages."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)


@register_connector("gmail")
class GmailConnector(BaseConnector):
    """Polls Gmail via Google API using historyId for incremental fetch."""

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll Gmail for new messages since historyId cursor."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            logger.warning("No access token for Gmail polling, user=%s", user_id)
            return [], cursor

        events = []
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                if cursor:
                    # Incremental: use history API
                    resp = await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/history",
                        params={"startHistoryId": cursor, "historyTypes": "messageAdded"},
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        new_cursor = data.get("historyId", cursor)
                        for history in data.get("history", []):
                            for msg_added in history.get("messagesAdded", []):
                                msg_id = msg_added["message"]["id"]
                                event = await self._fetch_message_as_event(
                                    client, access_token, user_id, msg_id
                                )
                                if event:
                                    events.append(event)
                    elif resp.status_code == 404:
                        # History expired, do full sync
                        cursor = None
                else:
                    # Initial: list recent messages
                    resp = await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                        params={"maxResults": 10, "q": "is:inbox newer_than:1d"},
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for msg_meta in data.get("messages", []):
                            event = await self._fetch_message_as_event(
                                client, access_token, user_id, msg_meta["id"]
                            )
                            if event:
                                events.append(event)

                    # Get current historyId for future incremental polling
                    profile = await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=10,
                    )
                    if profile.status_code == 200:
                        new_cursor = profile.json().get("historyId")

        except Exception:
            logger.warning("Gmail poll failed for user %s", user_id, exc_info=True)

        logger.info("Gmail poll: %d events, cursor %s -> %s", len(events), cursor, new_cursor)
        return events, new_cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        """Test Gmail connection."""
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return ConnectorHealth(
                        provider="gmail", status="healthy", last_poll_at=datetime.now(timezone.utc)
                    )
                return ConnectorHealth(
                    provider="gmail",
                    status="down",
                    last_poll_at=None,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(provider="gmail", status="down", last_poll_at=None, error=str(e))

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        """Get Google OAuth URL."""
        return "/v1/auth/oauth/google/authorize"

    async def _fetch_message_as_event(
        self, client, access_token: str, user_id: str, msg_id: str
    ) -> RawEvent | None:
        """Fetch a single Gmail message and convert to RawEvent."""
        resp = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None

        msg = resp.json()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        sender = headers.get("From", "unknown")
        subject = headers.get("Subject", "(no subject)")
        snippet = msg.get("snippet", "")

        return RawEvent(
            source="gmail",
            source_account_id="gmail_primary",
            event_type="email_received",
            entity_type="email_thread",
            entity_id=msg.get("threadId", msg_id),
            title=subject,
            summary=snippet[:500],
            actor={"type": "person", "email": sender, "name": sender},
            raw_payload={"message_id": msg_id, "labels": msg.get("labelIds", [])},
        )
