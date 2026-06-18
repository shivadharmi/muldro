"""Google Drive connector — polls for file changes and supports create/share actions."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_CHANGES_URL = "https://www.googleapis.com/drive/v3/changes"
DRIVE_START_PAGE_URL = "https://www.googleapis.com/drive/v3/changes/startPageToken"


@register_connector("drive")
class DriveConnector(BaseConnector):
    """Polls Google Drive API for recent file changes using change tokens."""

    cursor_type: str = "page_token"
    supports_actions: bool = True
    available_actions: list[str] = ["create_file", "share_file"]

    # TODO: migrate to PollResult (returns bare 2-tuple — LSP violation vs BaseConnector.poll)
    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Poll Drive for file changes since page token cursor.

        On first poll (no cursor), lists recent files and obtains a startPageToken.
        On subsequent polls, uses the Changes API for incremental fetch.
        """
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            logger.warning("No access token for Drive polling, user=%s", user_id)
            return [], cursor

        events: list[RawEvent] = []
        new_cursor = cursor

        try:
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {access_token}"}

                if cursor:
                    # Incremental: use Changes API
                    resp = await client.get(
                        DRIVE_CHANGES_URL,
                        params={
                            "pageToken": cursor,
                            "fields": "newStartPageToken,changes(fileId,file(id,name,mimeType,"
                            "modifiedTime,lastModifyingUser,webViewLink))",
                            "spaces": "drive",
                        },
                        headers=headers,
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        new_cursor = data.get("newStartPageToken", cursor)
                        for change in data.get("changes", []):
                            file_info = change.get("file")
                            if file_info:
                                event = self._file_to_event(file_info, "file_modified", user_id)
                                events.append(event)
                else:
                    # Initial: list recent files
                    resp = await client.get(
                        DRIVE_FILES_URL,
                        params={
                            "pageSize": 20,
                            "orderBy": "modifiedTime desc",
                            "fields": "files(id,name,mimeType,modifiedTime,"
                            "lastModifyingUser,webViewLink)",
                        },
                        headers=headers,
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for file_info in data.get("files", []):
                            event = self._file_to_event(file_info, "file_created", user_id)
                            events.append(event)

                    # Obtain startPageToken for future incremental polling
                    token_resp = await client.get(
                        DRIVE_START_PAGE_URL,
                        headers=headers,
                        timeout=10,
                    )
                    if token_resp.status_code == 200:
                        new_cursor = token_resp.json().get("startPageToken")

        except Exception:
            logger.warning("Drive poll failed for user %s", user_id, exc_info=True)

        logger.info("Drive poll: %d events, cursor %s -> %s", len(events), cursor, new_cursor)
        return events, new_cursor

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
