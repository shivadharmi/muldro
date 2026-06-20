"""Notion connector — polls for page updates and supports write actions (MCP fallback)."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.connectors.poll_result import PollErrorClass, PollResult, _classify_http_status
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {"Notion-Version": "2022-06-28", "Content-Type": "application/json"}

# Defensive page cap for a single poll. Notion's /v1/search paginates via
# ``has_more`` + ``next_cursor``; a misbehaving provider that always returns
# ``has_more: true`` would otherwise loop forever. On truncation we warn so
# silent data loss is visible, consistent with the gmail/github connectors.
MAX_PAGES = 50

# Map a Notion error-body ``code`` to a PollErrorClass to refine the status-only
# mapping. /v1/search only filters by object type (NOT a last_edited_time range),
# so the watermark is enforced client-side via ascending sort + pagination.
_NOTION_ERROR_CODE_CLASS: dict[str, PollErrorClass] = {
    "rate_limited": "rate_limited",
    "unauthorized": "auth_failed",
    "restricted_resource": "auth_failed",
    "object_not_found": "permanent",
}


@register_connector("notion")
class NotionConnector(BaseConnector):
    """Polls Notion API for recently edited pages. MCP server is the primary write path."""

    cursor_type: str = "since_timestamp"
    supports_actions: bool = True
    available_actions: list[str] = ["create_page", "update_page", "search"]

    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Poll Notion for pages edited since cursor (ISO last_edited_time watermark).

        Sorts ascending and paginates ``has_more``/``next_cursor`` to completion so
        edits beyond the first page are not missed; advances the cursor to the max
        processed ``last_edited_time``. Never advances the cursor on any error.
        """
        import httpx

        access_token = credentials.get("access_token", "")
        if not access_token:
            return PollResult(events=[], cursor=cursor, error_class="auth_failed")

        events: list[RawEvent] = []
        # Advance the cursor to the MAX last_edited_time across all processed pages —
        # NOT wall-clock now(). The boundary item may re-appear next poll (cursor is
        # an inclusive ``<=`` filter below); EventProcessor dedups on the idempotency
        # key, which now includes last_edited_time so distinct edits stay distinct.
        max_edited = cursor

        try:
            async with httpx.AsyncClient() as client:
                headers = {**NOTION_HEADERS, "Authorization": f"Bearer {access_token}"}
                start_cursor: str | None = None
                pages_fetched = 0
                truncated = False

                while True:
                    body: dict = {
                        "sort": {"direction": "ascending", "timestamp": "last_edited_time"},
                        "filter": {"property": "object", "value": "page"},
                        "page_size": 50,
                    }
                    if start_cursor:
                        body["start_cursor"] = start_cursor

                    resp = await client.post(
                        f"{NOTION_API}/search",
                        json=body,
                        headers=headers,
                        timeout=15,
                    )

                    if resp.status_code != 200:
                        error_class = self._classify_error_response(resp)
                        logger.warning(
                            "Notion search API returned %d (%s) for user %s",
                            resp.status_code,
                            error_class,
                            user_id,
                        )
                        # Cursor never advances on error.
                        return PollResult(events=[], cursor=cursor, error_class=error_class)

                    data = resp.json()
                    for result in data.get("results", []):
                        if result.get("object") != "page":
                            continue
                        edited = result.get("last_edited_time", "")
                        if cursor and edited <= cursor:
                            continue
                        event = self._normalize_page(result)
                        if event:
                            events.append(event)
                        if edited and (max_edited is None or edited > max_edited):
                            max_edited = edited

                    pages_fetched += 1
                    if not data.get("has_more"):
                        break
                    start_cursor = data.get("next_cursor")
                    if not start_cursor:
                        break
                    if pages_fetched >= MAX_PAGES:
                        truncated = True
                        break

                if truncated:
                    logger.warning(
                        "Notion poll truncated at %d pages for user %s; remaining pages "
                        "were not drained this poll",
                        MAX_PAGES,
                        user_id,
                    )

        except Exception:
            logger.warning("Notion poll failed for user %s", user_id, exc_info=True)
            return PollResult(events=[], cursor=cursor, error_class="transient")

        # Advance to the max processed last_edited_time; keep the incoming cursor when
        # nothing new was seen — never jump forward past unfetched data.
        new_cursor = max_edited if max_edited is not None else cursor

        logger.info("Notion poll: %d events, cursor %s -> %s", len(events), cursor, new_cursor)
        return PollResult(events=events, cursor=new_cursor)

    @staticmethod
    def _classify_error_response(resp) -> PollErrorClass:
        """Refine the status-only mapping using the Notion error body ``code`` field."""
        error_class = _classify_http_status(resp.status_code)
        try:
            data = resp.json()
        except Exception:
            return error_class
        if isinstance(data, dict) and data.get("object") == "error":
            refined = _NOTION_ERROR_CODE_CLASS.get(data.get("code", ""))
            if refined is not None:
                return refined
        return error_class

    async def test(self, credentials: dict) -> ConnectorHealth:
        import httpx

        access_token = credentials.get("access_token", "")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{NOTION_API}/users/me",
                    headers={**NOTION_HEADERS, "Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return ConnectorHealth(
                        provider="notion",
                        status="healthy",
                        last_poll_at=datetime.now(timezone.utc),
                    )
                return ConnectorHealth(
                    provider="notion",
                    status="down",
                    last_poll_at=None,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(
                provider="notion", status="down", last_poll_at=None, error=str(e)
            )

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return "/v1/auth/oauth/notion/authorize"

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        access_token = credentials.get("access_token", "")
        if not access_token:
            return {"status": "error", "error": "No access token"}

        dispatch = {
            "create_page": self._action_create_page,
            "update_page": self._action_update_page,
            "search": self._action_search,
        }
        return await dispatch[action](params, access_token)

    async def _action_create_page(self, params: dict, access_token: str) -> dict:
        import httpx

        parent_id = params.get("parent_id", "")
        title = params.get("title", "")
        if not parent_id:
            return {"status": "error", "error": "parent_id required"}

        body: dict = {
            "parent": {"database_id": parent_id},
            "properties": {
                "title": {"title": [{"text": {"content": title}}]},
            },
        }
        if params.get("content"):
            body["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": params["content"]}}]
                    },
                }
            ]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{NOTION_API}/pages",
                json=body,
                headers={**NOTION_HEADERS, "Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {"status": "ok", "page_id": data.get("id"), "url": data.get("url")}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_update_page(self, params: dict, access_token: str) -> dict:
        import httpx

        page_id = params.get("page_id", "")
        if not page_id:
            return {"status": "error", "error": "page_id required"}

        body: dict = {"properties": {}}
        if params.get("title"):
            body["properties"]["title"] = {"title": [{"text": {"content": params["title"]}}]}
        if params.get("archived") is not None:
            body["archived"] = params["archived"]

        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{NOTION_API}/pages/{page_id}",
                json=body,
                headers={**NOTION_HEADERS, "Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                return {"status": "ok", "page_id": page_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_search(self, params: dict, access_token: str) -> dict:
        import httpx

        query = params.get("query", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{NOTION_API}/search",
                json={"query": query, "page_size": params.get("limit", 10)},
                headers={**NOTION_HEADERS, "Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                items = []
                for r in results:
                    title_prop = r.get("properties", {}).get("title", {})
                    title_parts = title_prop.get("title", []) if title_prop else []
                    title = title_parts[0].get("plain_text", "") if title_parts else ""
                    items.append(
                        {
                            "id": r.get("id"),
                            "object": r.get("object"),
                            "title": title,
                            "url": r.get("url"),
                        }
                    )
                return {"status": "ok", "results": items}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    @staticmethod
    def _normalize_page(page: dict) -> RawEvent | None:
        title_prop = page.get("properties", {}).get("title", {})
        title_parts = title_prop.get("title", []) if title_prop else []
        title = title_parts[0].get("plain_text", "") if title_parts else "(untitled)"

        edited = page.get("last_edited_time", "")
        created = page.get("created_time", "")
        event_type = "page_created" if edited == created else "page_updated"

        occurred_at = None
        if edited:
            try:
                occurred_at = datetime.fromisoformat(edited.replace("Z", "+00:00"))
            except ValueError:
                pass

        edited_by = page.get("last_edited_by", {})

        return RawEvent(
            source="notion",
            source_account_id="notion_primary",
            event_type=event_type,
            entity_type="page",
            entity_id=page.get("id", ""),
            occurred_at=occurred_at,
            title=title,
            summary=f"Notion page: {title}",
            actor={
                "type": edited_by.get("type", "person"),
                "name": edited_by.get("name", ""),
            },
            raw_payload={
                "page_id": page.get("id"),
                "url": page.get("url"),
                "last_edited_time": edited,
            },
        )
