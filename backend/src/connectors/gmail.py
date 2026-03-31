"""Gmail connector — polls Gmail API for new messages."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)


@register_connector("gmail")
class GmailConnector(BaseConnector):
    """Polls Gmail via Google API using historyId for incremental fetch."""

    cursor_type: str = "history_id"
    supports_actions: bool = True
    available_actions: list[str] = [
        "list_unread",
        "get_message",
        "send_email",
        "create_draft",
        "archive",
        "mark_read",
    ]

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
            params={
                "format": "metadata",
                "metadataHeaders": [
                    "From",
                    "To",
                    "Cc",
                    "Subject",
                    "Date",
                    "Message-ID",
                    "In-Reply-To",
                    "References",
                ],
            },
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
            raw_payload={
                "message_id": msg_id,
                "labels": msg.get("labelIds", []),
                "to": headers.get("To", ""),
                "cc": headers.get("Cc", ""),
                "rfc_message_id": headers.get("Message-ID", ""),
                "in_reply_to": headers.get("In-Reply-To", ""),
                "references": headers.get("References", ""),
            },
        )

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        """Execute a Gmail write action."""
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "list_unread": self._action_list_unread,
            "get_message": self._action_get_message,
            "send_email": self._action_send_email,
            "create_draft": self._action_create_draft,
            "archive": self._action_archive,
            "mark_read": self._action_mark_read,
        }
        handler = dispatch[action]
        return await handler(params, access_token)

    async def _action_list_unread(self, params: dict, access_token: str) -> dict:
        """List unread emails from inbox."""
        import httpx

        max_results = params.get("max_results", 20)
        query = params.get("query", "is:inbox is:unread")

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"maxResults": max_results, "q": query},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if resp.status_code != 200:
                return {"status": "error", "error": f"HTTP {resp.status_code}"}

            data = resp.json()
            messages = []
            for msg_meta in data.get("messages", []):
                detail = await self._fetch_message_detail(client, access_token, msg_meta["id"])
                if detail:
                    messages.append(detail)

        return {"status": "ok", "emails": messages, "count": len(messages)}

    async def _action_get_message(self, params: dict, access_token: str) -> dict:
        """Get full message content by ID."""
        import httpx

        msg_id = params.get("message_id", "")
        if not msg_id:
            return {"status": "error", "error": "message_id required"}

        async with httpx.AsyncClient() as client:
            detail = await self._fetch_message_detail(client, access_token, msg_id)
            if not detail:
                return {"status": "error", "error": "Message not found"}
            return {"status": "ok", **detail}

    async def _action_send_email(self, params: dict, access_token: str) -> dict:
        """Send an email."""
        import base64

        import httpx

        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        thread_id = params.get("thread_id")

        if not to or not subject:
            return {"status": "error", "error": "to and subject required"}

        raw_message = (
            f"To: {to}\r\n"
            f"Subject: {subject}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            f"{body}"
        )
        encoded = base64.urlsafe_b64encode(raw_message.encode()).decode()

        payload = {"raw": encoded}
        if thread_id:
            payload["threadId"] = thread_id

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if resp.status_code in (200, 202):
                data = resp.json()
                return {
                    "status": "ok",
                    "message_id": data.get("id"),
                    "thread_id": data.get("threadId"),
                }
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    async def _action_create_draft(self, params: dict, access_token: str) -> dict:
        """Create an email draft."""
        import base64

        import httpx

        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        thread_id = params.get("thread_id")

        raw_message = (
            f"To: {to}\r\n"
            f"Subject: {subject}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            f"{body}"
        )
        encoded = base64.urlsafe_b64encode(raw_message.encode()).decode()

        message_body = {"raw": encoded}
        if thread_id:
            message_body["threadId"] = thread_id

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                json={"message": message_body},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "status": "ok",
                    "draft_id": data.get("id"),
                    "message_id": data.get("message", {}).get("id"),
                }
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    async def _action_archive(self, params: dict, access_token: str) -> dict:
        """Archive a message (remove INBOX label)."""
        import httpx

        msg_id = params.get("message_id", "")
        if not msg_id:
            return {"status": "error", "error": "message_id required"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}/modify",
                json={"removeLabelIds": ["INBOX"]},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return {"status": "ok", "message_id": msg_id, "action": "archived"}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    async def _action_mark_read(self, params: dict, access_token: str) -> dict:
        """Mark a message as read (remove UNREAD label)."""
        import httpx

        msg_id = params.get("message_id", "")
        if not msg_id:
            return {"status": "error", "error": "message_id required"}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}/modify",
                json={"removeLabelIds": ["UNREAD"]},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return {"status": "ok", "message_id": msg_id, "action": "marked_read"}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    async def _fetch_message_detail(self, client, access_token: str, msg_id: str) -> dict | None:
        """Fetch a message with headers + snippet for listing."""
        resp = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
            params={
                "format": "metadata",
                "metadataHeaders": [
                    "From",
                    "To",
                    "Cc",
                    "Subject",
                    "Date",
                    "Message-ID",
                    "In-Reply-To",
                    "References",
                ],
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None

        msg = resp.json()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        return {
            "message_id": msg_id,
            "thread_id": msg.get("threadId"),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
            "rfc_message_id": headers.get("Message-ID", ""),
            "in_reply_to": headers.get("In-Reply-To", ""),
            "references": headers.get("References", ""),
        }
