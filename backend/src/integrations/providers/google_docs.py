"""Google Docs native adapter — create, read, update, append."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

DOCS_API_BASE = "https://docs.googleapis.com/v1/documents"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


@dataclass(frozen=True, slots=True)
class DocInfo:
    document_id: str
    title: str
    revision_id: str | None = None
    body_text: str | None = None


class GoogleDocsAdapter:
    """Direct Docs API v1 adapter using OAuth access token."""

    def __init__(self, access_token: str):
        self._token = access_token
        self._headers = {"Authorization": f"Bearer {access_token}"}

    async def create(self, title: str, body_text: str | None = None) -> DocInfo:
        """Create a new Google Doc with optional initial text."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                DOCS_API_BASE,
                headers={**self._headers, "Content-Type": "application/json"},
                json={"title": title},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            doc_id = data["documentId"]

        if body_text:
            await self.append_text(doc_id, body_text)

        return DocInfo(
            document_id=doc_id,
            title=data.get("title", title),
            revision_id=data.get("revisionId"),
        )

    async def get(self, document_id: str) -> DocInfo:
        """Get document metadata and plain-text body."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DOCS_API_BASE}/{document_id}",
                headers=self._headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        body_text = _extract_text(data.get("body", {}))
        return DocInfo(
            document_id=data["documentId"],
            title=data.get("title", ""),
            revision_id=data.get("revisionId"),
            body_text=body_text,
        )

    async def append_text(self, document_id: str, text: str) -> None:
        """Append text to the end of a document."""
        requests = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": text,
                }
            }
        ]
        await self._batch_update(document_id, requests)

    async def replace_text(self, document_id: str, find: str, replace: str) -> int:
        """Find-and-replace text in a document. Returns number of replacements."""
        requests = [
            {
                "replaceAllText": {
                    "containsText": {"text": find, "matchCase": True},
                    "replaceText": replace,
                }
            }
        ]
        result = await self._batch_update(document_id, requests)
        replies = result.get("replies", [])
        if replies and "replaceAllText" in replies[0]:
            return replies[0]["replaceAllText"].get("occurrencesChanged", 0)
        return 0

    async def insert_text(self, document_id: str, text: str, index: int = 1) -> None:
        """Insert text at a specific index."""
        requests = [{"insertText": {"location": {"index": index}, "text": text}}]
        await self._batch_update(document_id, requests)

    async def delete(self, document_id: str) -> None:
        """Delete a document (moves to trash via Drive API)."""
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{DRIVE_API_BASE}/files/{document_id}",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"trashed": True},
                timeout=30.0,
            )
            resp.raise_for_status()

    async def _batch_update(self, document_id: str, requests: list[dict]) -> dict:
        """Execute a batchUpdate on a document."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{DOCS_API_BASE}/{document_id}:batchUpdate",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"requests": requests},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()


def _extract_text(body: dict) -> str:
    """Extract plain text from a Docs body structure."""
    parts: list[str] = []
    for element in body.get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run:
                parts.append(text_run.get("content", ""))
    return "".join(parts)
