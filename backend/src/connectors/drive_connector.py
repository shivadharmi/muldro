"""Google Drive connector — polls for file changes and supports create/share actions."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.connectors.poll_result import PollResult, _classify_http_status
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_CHANGES_URL = "https://www.googleapis.com/drive/v3/changes"
DRIVE_START_PAGE_URL = "https://www.googleapis.com/drive/v3/changes/startPageToken"

# Defensive page cap for a single incremental sync. Google Drive returns
# newStartPageToken ONLY on the final page; intermediate pages carry
# nextPageToken. A misbehaving provider that always returns a nextPageToken
# would otherwise loop forever. On truncation we log a warning so silent data
# loss is visible, consistent with the gmail/calendar MAX_PAGES caps.
MAX_PAGES = 50


@register_connector("drive")
class DriveConnector(BaseConnector):
    """Polls Google Drive API for recent file changes using change tokens."""

    cursor_type: str = "page_token"
    supports_actions: bool = True
    available_actions: list[str] = ["create_file", "share_file"]

    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Poll Drive for file changes since page token cursor.

        On first poll (no cursor), lists recent files and obtains a startPageToken.
        On subsequent polls, walks the Changes API (following nextPageToken) for an
        incremental fetch. An expired pageToken (HTTP 410) triggers a re-init via the
        first-poll path rather than a silent stall, mirroring calendar's 410 handling.
        """
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            logger.warning("No access token for Drive polling, user=%s", user_id)
            return PollResult(events=[], cursor=cursor, error_class="auth_failed")

        try:
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {access_token}"}
                if cursor:
                    return await self._poll_changes(client, headers, user_id, cursor, credentials)
                return await self._poll_initial(client, headers, user_id, cursor)
        except Exception:
            logger.warning("Drive poll failed for user %s", user_id, exc_info=True)
            return PollResult(events=[], cursor=cursor, error_class="transient")

    async def _poll_changes(
        self, client, headers: dict, user_id: str, cursor: str, credentials: dict
    ) -> PollResult:
        """Incremental fetch via the Changes API, paginating to the final page.

        Google returns ``newStartPageToken`` ONLY on the last page; intermediate
        pages carry ``nextPageToken``. The cursor advances ONLY to that final
        ``newStartPageToken`` after every page has been drained.
        """
        events: list[RawEvent] = []
        new_cursor = cursor
        page_token = cursor
        pages_fetched = 0
        truncated = False

        while True:
            resp = await client.get(
                DRIVE_CHANGES_URL,
                params={
                    "pageToken": page_token,
                    "includeRemoved": "true",
                    "fields": "newStartPageToken,nextPageToken,changes(fileId,removed,"
                    "file(id,name,mimeType,modifiedTime,trashed,lastModifyingUser,webViewLink))",
                    "spaces": "drive",
                },
                headers=headers,
                timeout=15,
            )

            if resp.status_code == 410:
                # pageToken expired — re-init via the first-poll path (cursor=None),
                # mirroring calendar's 410 syncToken handling. The first-poll path uses
                # different endpoints (files.list + startPageToken), so this single
                # re-entry cannot itself 410 here and recurse forever.
                return await self.poll(user_id, None, credentials)

            if resp.status_code != 200:
                error_class = _classify_http_status(resp.status_code)
                logger.warning(
                    "Drive changes API returned %d for user %s", resp.status_code, user_id
                )
                # Return unchanged incoming cursor on failure
                return PollResult(events=[], cursor=cursor, error_class=error_class)

            data = resp.json()
            for change in data.get("changes", []):
                event = self._change_to_event(change, user_id)
                if event:
                    events.append(event)

            # newStartPageToken only appears on the final page; capture it so the
            # cursor advances correctly after the loop.
            final_token = data.get("newStartPageToken")
            if final_token:
                new_cursor = final_token

            pages_fetched += 1
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            if pages_fetched >= MAX_PAGES:
                truncated = True
                break

        if truncated:
            logger.warning(
                "Drive changes sync truncated at %d pages for user %s; remaining "
                "changes were not drained this poll",
                MAX_PAGES,
                user_id,
            )

        logger.info("Drive poll: %d events, cursor %s -> %s", len(events), cursor, new_cursor)
        return PollResult(events=events, cursor=new_cursor)

    async def _poll_initial(
        self, client, headers: dict, user_id: str, cursor: str | None
    ) -> PollResult:
        """First-poll path: list recent files, then obtain a startPageToken cursor."""
        events: list[RawEvent] = []
        new_cursor = cursor

        resp = await client.get(
            DRIVE_FILES_URL,
            params={
                "pageSize": 20,
                "orderBy": "modifiedTime desc",
                "fields": "files(id,name,mimeType,modifiedTime,lastModifyingUser,webViewLink)",
            },
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            error_class = _classify_http_status(resp.status_code)
            logger.warning("Drive files API returned %d for user %s", resp.status_code, user_id)
            return PollResult(events=[], cursor=cursor, error_class=error_class)

        for file_info in resp.json().get("files", []):
            events.append(self._file_to_event(file_info, "file_created", user_id))

        # Obtain startPageToken for future incremental polling. A failure here leaves
        # the cursor unusable, so the poll is a transient failure — NOT a success with
        # a null cursor (which would re-trigger a full sync every poll).
        token_resp = await client.get(DRIVE_START_PAGE_URL, headers=headers, timeout=10)
        if token_resp.status_code != 200:
            error_class = _classify_http_status(token_resp.status_code)
            logger.warning(
                "Drive startPageToken returned %d for user %s after list; treating as failure",
                token_resp.status_code,
                user_id,
            )
            # Fail -> empty events + INCOMING cursor unchanged. The files.list page
            # drained above is discarded: the consumer drops events on any failure
            # and never advances the cursor on a failing poll, so emitting partial
            # events here would be silently dropped. The next clean poll re-lists.
            return PollResult(events=[], cursor=cursor, error_class=error_class)
        new_cursor = token_resp.json().get("startPageToken")

        logger.info("Drive poll: %d events, cursor %s -> %s", len(events), cursor, new_cursor)
        return PollResult(events=events, cursor=new_cursor)

    async def test(self, credentials: dict) -> ConnectorHealth:
        """Test Drive connection by fetching about info."""
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.googleapis.com/drive/v3/about",
                    params={"fields": "user"},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                status = "healthy" if resp.status_code == 200 else "down"
                return ConnectorHealth(
                    provider="drive",
                    status=status,
                    last_poll_at=datetime.now(timezone.utc) if status == "healthy" else None,
                    error=None if status == "healthy" else f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(provider="drive", status="down", last_poll_at=None, error=str(e))

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/google/authorize"

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        """Execute a Drive write action.

        Supported actions:
          - create_file: params = {name, mime_type?, content?}
          - share_file: params = {file_id, email, role?}
        """
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        if action not in self.available_actions:
            return {"status": "error", "error": f"Unsupported action: {action}"}

        try:
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {access_token}"}

                if action == "create_file":
                    return await self._action_create_file(client, headers, params)
                elif action == "share_file":
                    return await self._action_share_file(client, headers, params)

        except Exception as e:
            logger.error("Drive action '%s' failed: %s", action, e, exc_info=True)
            return {"status": "error", "error": str(e)}

        return {"status": "error", "error": "Unknown action"}

    # --- Private helpers ---

    async def _action_create_file(self, client, headers: dict, params: dict) -> dict:
        """Create a file (metadata only) on Google Drive."""
        name = params.get("name", "Untitled")
        mime_type = params.get("mime_type", "application/vnd.google-apps.document")

        resp = await client.post(
            DRIVE_FILES_URL,
            json={"name": name, "mimeType": mime_type},
            headers={**headers, "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return {"status": "ok", "file_id": data.get("id"), "name": data.get("name")}
        return {"status": "error", "error": f"HTTP {resp.status_code}", "body": resp.text}

    async def _action_share_file(self, client, headers: dict, params: dict) -> dict:
        """Share a file by creating a permission."""
        file_id = params.get("file_id")
        email = params.get("email")
        role = params.get("role", "reader")

        if not file_id or not email:
            return {"status": "error", "error": "file_id and email are required"}

        resp = await client.post(
            f"{DRIVE_FILES_URL}/{file_id}/permissions",
            json={"type": "user", "role": role, "emailAddress": email},
            headers={**headers, "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return {"status": "ok", "file_id": file_id, "shared_with": email, "role": role}
        return {"status": "error", "error": f"HTTP {resp.status_code}", "body": resp.text}

    def _change_to_event(self, change: dict, user_id: str) -> RawEvent | None:
        """Convert a Drive change record to a RawEvent.

        A change with ``removed: true`` (no ``file``) or a file whose ``trashed``
        flag is set emits a ``file_removed`` event keyed on the change's ``fileId``.
        Present files emit ``file_modified``.
        """
        file_info = change.get("file")
        if change.get("removed") or (file_info and file_info.get("trashed")):
            return self._removed_to_event(change, file_info, user_id)
        if file_info:
            return self._file_to_event(file_info, "file_modified", user_id)
        return None

    @staticmethod
    def _removed_to_event(change: dict, file_info: dict | None, user_id: str) -> RawEvent:
        """Build a file_removed RawEvent from a change record."""
        file_id = change.get("fileId", "")
        name = (file_info or {}).get("name", "Untitled")
        return RawEvent(
            source="drive",
            source_account_id="drive_primary",
            event_type="file_removed",
            entity_type="file",
            entity_id=file_id,
            occurred_at=None,
            title=name,
            summary=f"{name} (removed)",
            actor={"type": "person", "email": "", "name": ""},
            raw_payload={"file_id": file_id, "removed": True},
        )

    @staticmethod
    def _file_to_event(file_info: dict, event_type: str, user_id: str) -> RawEvent:
        """Convert a Drive file dict to a RawEvent."""
        name = file_info.get("name", "Untitled")
        file_id = file_info.get("id", "")
        mime_type = file_info.get("mimeType", "")
        modified_time = file_info.get("modifiedTime", "")
        modifier = file_info.get("lastModifyingUser", {})
        web_link = file_info.get("webViewLink", "")

        occurred_at = None
        if modified_time:
            try:
                occurred_at = datetime.fromisoformat(modified_time.replace("Z", "+00:00"))
            except ValueError:
                pass

        return RawEvent(
            source="drive",
            source_account_id="drive_primary",
            event_type=event_type,
            entity_type="file",
            entity_id=file_id,
            occurred_at=occurred_at,
            title=name,
            summary=f"{name} ({mime_type})",
            actor={
                "type": "person",
                "email": modifier.get("emailAddress", ""),
                "name": modifier.get("displayName", ""),
            },
            raw_payload={
                "file_id": file_id,
                "mime_type": mime_type,
                "web_view_link": web_link,
            },
        )
