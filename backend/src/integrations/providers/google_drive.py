"""Google Drive native adapter — list, search, upload, download, share."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3"


@dataclass(frozen=True, slots=True)
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    size: int | None = None
    created_time: datetime | None = None
    modified_time: datetime | None = None
    web_view_link: str | None = None
    parents: list[str] | None = None
    shared: bool = False


class GoogleDriveAdapter:
    """Direct Drive API v3 adapter using OAuth access token."""

    def __init__(self, access_token: str):
        self._token = access_token
        self._headers = {"Authorization": f"Bearer {access_token}"}

    async def list_files(
        self,
        query: str | None = None,
        page_size: int = 20,
        page_token: str | None = None,
        order_by: str = "modifiedTime desc",
    ) -> dict:
        """List files with optional query filter."""
        params: dict = {
            "pageSize": page_size,
            "orderBy": order_by,
            "fields": "nextPageToken,files(id,name,mimeType,size,createdTime,"
            "modifiedTime,webViewLink,parents,shared)",
        }
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DRIVE_API_BASE}/files",
                headers=self._headers,
                params=params,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        files = [_parse_file(f) for f in data.get("files", [])]
        return {
            "files": files,
            "next_page_token": data.get("nextPageToken"),
        }

    async def search(self, query: str, page_size: int = 20) -> list[DriveFile]:
        """Search files by name or full-text content."""
        escaped = query.replace("'", "\\'")
        q = f"fullText contains '{escaped}' and trashed = false"
        result = await self.list_files(query=q, page_size=page_size)
        return result["files"]

    async def get_file(self, file_id: str) -> DriveFile:
        """Get file metadata by ID."""
        params = {
            "fields": "id,name,mimeType,size,createdTime,modifiedTime,webViewLink,parents,shared",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DRIVE_API_BASE}/files/{file_id}",
                headers=self._headers,
                params=params,
                timeout=30.0,
            )
            resp.raise_for_status()
            return _parse_file(resp.json())

    async def download_content(self, file_id: str) -> bytes:
        """Download file content (non-Google formats only)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DRIVE_API_BASE}/files/{file_id}",
                headers=self._headers,
                params={"alt": "media"},
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.content

    async def export(self, file_id: str, mime_type: str = "text/plain") -> bytes:
        """Export a Google Workspace file (Docs/Sheets/Slides) to a given format."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DRIVE_API_BASE}/files/{file_id}/export",
                headers=self._headers,
                params={"mimeType": mime_type},
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.content

    async def create_file(
        self,
        name: str,
        mime_type: str = "application/octet-stream",
        content: bytes | None = None,
        parent_id: str | None = None,
    ) -> DriveFile:
        """Create a new file (metadata + optional content)."""
        metadata: dict = {"name": name, "mimeType": mime_type}
        if parent_id:
            metadata["parents"] = [parent_id]

        if content:
            # Multipart upload
            import json

            boundary = "muldro_upload_boundary"
            body = (
                (
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{json.dumps(metadata)}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode()
                + content
                + f"\r\n--{boundary}--".encode()
            )

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{UPLOAD_API_BASE}/files",
                    headers={
                        **self._headers,
                        "Content-Type": f"multipart/related; boundary={boundary}",
                    },
                    params={"uploadType": "multipart", "fields": "id,name,mimeType"},
                    content=body,
                    timeout=60.0,
                )
                resp.raise_for_status()
                return _parse_file(resp.json())
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{DRIVE_API_BASE}/files",
                    headers={**self._headers, "Content-Type": "application/json"},
                    json=metadata,
                    timeout=30.0,
                )
                resp.raise_for_status()
                return _parse_file(resp.json())

    async def delete_file(self, file_id: str) -> None:
        """Move a file to trash."""
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{DRIVE_API_BASE}/files/{file_id}",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"trashed": True},
                timeout=30.0,
            )
            resp.raise_for_status()

    async def share(self, file_id: str, email: str, role: str = "reader") -> dict:
        """Share a file with a user."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{DRIVE_API_BASE}/files/{file_id}/permissions",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"type": "user", "role": role, "emailAddress": email},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()


def _parse_file(data: dict) -> DriveFile:
    return DriveFile(
        file_id=data.get("id", ""),
        name=data.get("name", ""),
        mime_type=data.get("mimeType", ""),
        size=int(data["size"]) if data.get("size") else None,
        created_time=(
            datetime.fromisoformat(data["createdTime"]) if data.get("createdTime") else None
        ),
        modified_time=(
            datetime.fromisoformat(data["modifiedTime"]) if data.get("modifiedTime") else None
        ),
        web_view_link=data.get("webViewLink"),
        parents=data.get("parents"),
        shared=data.get("shared", False),
    )
