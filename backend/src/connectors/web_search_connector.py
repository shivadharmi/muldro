"""Web search connector — executes web searches via Brave or Serper APIs."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
SERPER_SEARCH_URL = "https://google.serper.dev/search"


@register_connector("web_search")
class WebSearchConnector(BaseConnector):
    """Executes web searches. Supports Brave Search API and Serper API.

    This is an action-only connector — polling is not applicable.
    Configure which provider to use via credentials:
      - {"provider": "brave", "api_key": "..."}
      - {"provider": "serper", "api_key": "..."}
    """

    supports_actions: bool = True
    available_actions: list[str] = ["search_web"]

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Not applicable for web search — raises NotImplementedError."""
        raise NotImplementedError("WebSearchConnector does not support polling")

    async def test(self, credentials: dict) -> ConnectorHealth:
        """Test the search API connection with a simple query."""
        import httpx

        provider = credentials.get("provider", "brave")
        api_key = credentials.get("api_key", "")

        if not api_key:
            return ConnectorHealth(
                provider="web_search",
                status="down",
                last_poll_at=None,
                error="No api_key provided",
            )

        try:
            async with httpx.AsyncClient() as client:
                if provider == "serper":
                    resp = await client.post(
                        SERPER_SEARCH_URL,
                        json={"q": "test", "num": 1},
                        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                        timeout=10,
                    )
                else:
                    resp = await client.get(
                        BRAVE_SEARCH_URL,
                        params={"q": "test", "count": 1},
                        headers={
                            "Accept": "application/json",
                            "Accept-Encoding": "gzip",
                            "X-Subscription-Token": api_key,
                        },
                        timeout=10,
                    )

                status = "healthy" if resp.status_code == 200 else "down"
                return ConnectorHealth(
                    provider="web_search",
                    status=status,
                    last_poll_at=datetime.now(timezone.utc) if status == "healthy" else None,
                    error=None if status == "healthy" else f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(
                provider="web_search", status="down", last_poll_at=None, error=str(e)
            )

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        """Web search uses API keys, not OAuth."""
        return ""

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        """Execute a web search action.

        Supported actions:
          - search_web: params = {query, num_results?, provider?}
            provider in credentials overrides params-level provider.
        """
        if action != "search_web":
            return {"status": "error", "error": f"Unsupported action: {action}"}

        query = params.get("query", "")
        if not query:
            return {"status": "error", "error": "query is required"}

        num_results = params.get("num_results", 10)
        # Provider from credentials takes precedence, then params, then default
        provider = credentials.get("provider") or params.get("provider", "brave")
        api_key = credentials.get("api_key", "")

        if not api_key:
            return {"status": "error", "error": "No api_key in credentials"}

        try:
            if provider == "serper":
                return await self._search_serper(query, num_results, api_key)
            else:
                return await self._search_brave(query, num_results, api_key)
        except Exception as e:
            logger.error("Web search failed (provider=%s): %s", provider, e, exc_info=True)
            return {"status": "error", "error": str(e)}

    # --- Provider implementations ---

    async def _search_brave(self, query: str, num_results: int, api_key: str) -> dict:
        """Search using Brave Search API."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                BRAVE_SEARCH_URL,
                params={"q": query, "count": min(num_results, 20)},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
                timeout=15,
            )

            if resp.status_code != 200:
                return {"status": "error", "error": f"Brave API returned HTTP {resp.status_code}"}

            data = resp.json()
            web_results = data.get("web", {}).get("results", [])
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "description": r.get("description", ""),
                }
                for r in web_results
            ]

            return {
                "status": "ok",
                "provider": "brave",
                "query": query,
                "results": results,
                "total": len(results),
            }

    async def _search_serper(self, query: str, num_results: int, api_key: str) -> dict:
        """Search using Serper (Google Search) API."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SERPER_SEARCH_URL,
                json={"q": query, "num": min(num_results, 100)},
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                timeout=15,
            )

            if resp.status_code != 200:
                return {"status": "error", "error": f"Serper API returned HTTP {resp.status_code}"}

            data = resp.json()
            organic = data.get("organic", [])
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "description": r.get("snippet", ""),
                }
                for r in organic
            ]

            return {
                "status": "ok",
                "provider": "serper",
                "query": query,
                "results": results,
                "total": len(results),
            }
