"""Gmail connector — polls Gmail API for new messages."""

import asyncio
import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.connectors.poll_result import PollResult, _classify_http_status
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

# Max pages of messages.list to walk during an initial full sync. Bounds the
# backfill so a brand-new connection cannot fan out unboundedly; when the bound
# truncates, we log a warning so silent data loss is visible.
MAX_BACKFILL_PAGES = 4

# Max pages of history.list to walk during an incremental sync. Active mailboxes
# can legitimately span many history pages, so this is set generous (much larger
# than MAX_BACKFILL_PAGES) — it should never truncate a realistic mailbox. It is
# purely a defensive cap: a buggy/abusive provider that always returns a
# nextPageToken would otherwise loop forever (with N per-message detail GETs per
# iteration). On hitting the cap we warn and advance the cursor to the last
# fetched historyId, so the next poll resumes from there.
MAX_HISTORY_PAGES = 50

# Per-message detail GETs within a single page are fetched concurrently, bounded
# by this semaphore. The whole poll runs under a 30s budget (connector_poller);
# serial per-message fetches make wall-clock scale with message count and blow
# that budget on a busy mailbox. Kept modest so a page fan-out cannot hammer the
# Gmail API rate limit.
MAX_CONCURRENT_MESSAGE_FETCHES = 8


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

    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Poll Gmail for new messages since historyId cursor."""
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            logger.warning("No access token for Gmail polling, user=%s", user_id)
            return PollResult(events=[], cursor=cursor, error_class="auth_failed")

        events: list[RawEvent] = []
        seen_msg_ids: set[str] = set()
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                if cursor:
                    # Incremental: use history API. Walk every page of nextPageToken
                    # before advancing the cursor — page 2+ messagesAdded would be lost
                    # forever otherwise (cursor jumps past data never fetched).
                    final_history_id = cursor
                    page_token: str | None = None
                    pages_fetched = 0
                    truncated = False
                    while True:
                        params = {
                            "startHistoryId": cursor,
                            "historyTypes": "messageAdded",
                        }
                        if page_token:
                            params["pageToken"] = page_token
                        resp = await client.get(
                            "https://gmail.googleapis.com/gmail/v1/users/me/history",
                            params=params,
                            headers={"Authorization": f"Bearer {access_token}"},
                            timeout=15,
                        )
                        if resp.status_code == 404:
                            # historyId expired — recurse into full sync (cursor=None).
                            # Mirrors calendar.py's 410 syncToken handling. The full-sync
                            # path uses different endpoints (messages.list + profile, not
                            # history.list), so this single re-entry cannot itself 404
                            # here and recurse forever.
                            return await self.poll(user_id, None, credentials)
                        if resp.status_code != 200:
                            error_class = _classify_http_status(resp.status_code)
                            logger.warning(
                                "Gmail history API returned %d for user %s",
                                resp.status_code,
                                user_id,
                            )
                            # Return unchanged incoming cursor on failure
                            return PollResult(events=[], cursor=cursor, error_class=error_class)

                        data = resp.json()
                        final_history_id = data.get("historyId", final_history_id)
                        page_msg_ids: list[str] = []
                        for history in data.get("history", []):
                            for msg_added in history.get("messagesAdded", []):
                                msg_id = msg_added["message"]["id"]
                                if msg_id in seen_msg_ids:
                                    continue
                                seen_msg_ids.add(msg_id)
                                page_msg_ids.append(msg_id)
                        events.extend(
                            await self._fetch_messages_as_events(
                                client, access_token, user_id, page_msg_ids
                            )
                        )

                        pages_fetched += 1
                        page_token = data.get("nextPageToken")
                        if not page_token:
                            break
                        if pages_fetched >= MAX_HISTORY_PAGES:
                            truncated = True
                            break

                    if truncated:
                        logger.warning(
                            "Gmail incremental sync truncated at %d pages for user %s; "
                            "remaining history was not drained this poll — next poll "
                            "resumes from historyId %s",
                            MAX_HISTORY_PAGES,
                            user_id,
                            final_history_id,
                        )

                    # Only advance the cursor after all pages were consumed (or the
                    # defensive page cap was hit). Advancing to the last fetched
                    # historyId on truncation is consistent with normal completion;
                    # the warning above surfaces that we may not have drained
                    # everything, and the next poll continues from here.
                    new_cursor = final_history_id
                else:
                    # Initial: list recent messages, following nextPageToken up to a
                    # bounded number of pages so the first sync cannot fan out forever.
                    page_token: str | None = None
                    pages_fetched = 0
                    truncated = False
                    while True:
                        params = {"maxResults": 25, "q": "is:inbox newer_than:3d"}
                        if page_token:
                            params["pageToken"] = page_token
                        resp = await client.get(
                            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                            params=params,
                            headers={"Authorization": f"Bearer {access_token}"},
                            timeout=15,
                        )
                        if resp.status_code != 200:
                            error_class = _classify_http_status(resp.status_code)
                            logger.warning(
                                "Gmail messages API returned %d for user %s",
                                resp.status_code,
                                user_id,
                            )
                            return PollResult(events=[], cursor=cursor, error_class=error_class)

                        data = resp.json()
                        page_msg_ids = []
                        for msg_meta in data.get("messages", []):
                            msg_id = msg_meta["id"]
                            if msg_id in seen_msg_ids:
                                continue
                            seen_msg_ids.add(msg_id)
                            page_msg_ids.append(msg_id)
                        events.extend(
                            await self._fetch_messages_as_events(
                                client, access_token, user_id, page_msg_ids
                            )
                        )

                        pages_fetched += 1
                        page_token = data.get("nextPageToken")
                        if not page_token:
                            break
                        if pages_fetched >= MAX_BACKFILL_PAGES:
                            truncated = True
                            break

                    if truncated:
                        logger.warning(
                            "Gmail initial sync truncated at %d pages for user %s; "
                            "older inbox messages were not backfilled",
                            MAX_BACKFILL_PAGES,
                            user_id,
                        )

                    # Get current historyId for future incremental polling.
                    # A profile failure here leaves new_cursor unusable (None/stale),
                    # so the poll is a transient failure — NOT a success with a null
                    # cursor (which would re-trigger a full sync every poll).
                    profile = await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=10,
                    )
                    if profile.status_code == 200:
                        new_cursor = profile.json().get("historyId")
                    else:
                        logger.warning(
                            "Gmail profile API returned %d for user %s after list; "
                            "treating poll as transient",
                            profile.status_code,
                            user_id,
                        )
                        return PollResult(events=events, cursor=cursor, error_class="transient")

        except Exception:
            logger.warning("Gmail poll failed for user %s", user_id, exc_info=True)
            return PollResult(events=[], cursor=cursor, error_class="transient")

        logger.info("Gmail poll: %d events, cursor %s -> %s", len(events), cursor, new_cursor)
        return PollResult(events=events, cursor=new_cursor)

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

    async def _fetch_messages_as_events(
        self, client, access_token: str, user_id: str, msg_ids: list[str]
    ) -> list[RawEvent]:
        """Fetch multiple message details concurrently, bounded by a semaphore.

        Preserves ``msg_ids`` order (gather is order-preserving) so event ordering
        matches the previous serial behaviour, and drops any message that failed
        to fetch (``None``).
        """
        if not msg_ids:
            return []
        sem = asyncio.Semaphore(MAX_CONCURRENT_MESSAGE_FETCHES)

        async def _one(msg_id: str) -> RawEvent | None:
            async with sem:
                return await self._fetch_message_as_event(client, access_token, user_id, msg_id)

        results = await asyncio.gather(*(_one(m) for m in msg_ids))
        return [event for event in results if event]

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
                    "List-Unsubscribe",
                    "List-Id",
                    "Precedence",
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

        # Bulk-mail signal headers for triage's deterministic pre-pass
        # (classify_by_rules in src/services/triage.py) — captured separately
        # (not the whole header list) so marketing/newsletter mail can be
        # skipped for free, without an LLM call.
        bulk_mail_header_names = {"list-unsubscribe", "list-id", "precedence"}
        captured_headers = {
            name: value for name, value in headers.items() if name.lower() in bulk_mail_header_names
        }

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
                "headers": captured_headers,
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
