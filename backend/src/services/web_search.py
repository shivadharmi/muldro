"""Composite web search tool — one HTTPS GET against DuckDuckGo's HTML endpoint.

This used to drive the Playwright MCP server (``browser_navigate`` + ``browser_snapshot``)
and regex an accessibility snapshot. The endpoint it targets has always been the
*non-JavaScript* HTML one, so the headless browser was pure transport overhead: it spawned
an `npx` subprocess and rendered a page in order to fetch static markup, then parsed a
lossy accessibility rendering of that markup instead of the markup itself.

Now it is a plain ``httpx`` GET parsed with the stdlib HTML parser. No browser, no MCP
server, no new dependency. The returned shape is unchanged — the tool layer and every
caller of the ``search.web`` capability see exactly what they saw before.
"""

import logging
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

logger = logging.getLogger(__name__)

# DuckDuckGo HTML endpoint — bot-friendly, no JavaScript needed, no CAPTCHAs
_DDG_URL = "https://html.duckduckgo.com/html/?q={query}"

_TIMEOUT_SECONDS = 15.0
_MAX_SNIPPET_CHARS = 500

# DDG rejects the default httpx user agent; this is the one concession to being a client.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _normalize(text: str) -> str:
    """Collapse the whitespace that HTML indentation introduces into single spaces."""
    return " ".join(text.split())


def _unwrap_ddg_url(href: str) -> str:
    """Resolve DuckDuckGo's ``/l/?uddg=<encoded>`` redirect wrapper to its destination.

    Every organic result href is a wrapper. Returning it unresolved would hand each
    consumer — memories, entity extraction, the Perceiver's findings — a duckduckgo.com
    URL in place of the source, which is both useless as provenance and wrong as a
    citation. Non-wrapper hrefs pass through, with protocol-relative URLs made absolute.
    """
    if not href:
        return ""
    candidate = f"https:{href}" if href.startswith("//") else href
    parsed = urlparse(candidate)
    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return candidate


class _ResultParser(HTMLParser):
    """Collect ``(title, url, snippet)`` triples from a DuckDuckGo HTML result page.

    DDG marks each organic result with two anchors: ``result__a`` carries the title text
    and the wrapped destination, and a following ``result__snippet`` carries the summary.
    Both may contain nested markup (``<b>`` on matched terms), so text is accumulated
    across ``handle_data`` calls rather than read from a single one.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._capturing: str | None = None  # "title" | "snippet" | None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        classes = (dict(attrs).get("class") or "").split()
        if "result__a" in classes:
            self._flush()
            self.results.append(
                {"title": "", "url": _unwrap_ddg_url(dict(attrs).get("href") or ""), "snippet": ""}
            )
            self._capturing = "title"
        elif "result__snippet" in classes and self.results:
            self._flush()
            self._capturing = "snippet"

    def handle_endtag(self, tag: str) -> None:
        # Nested <b>/<span> inside an anchor must not end the capture — only the anchor does.
        if tag == "a":
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buffer.append(data)

    def _flush(self) -> None:
        if self._capturing and self.results:
            text = _normalize("".join(self._buffer))
            if self._capturing == "title":
                self.results[-1]["title"] = text
            else:
                self.results[-1]["snippet"] = text[:_MAX_SNIPPET_CHARS]
        self._capturing = None
        self._buffer = []


def _parse_results(html: str, max_results: int) -> list[dict]:
    """Parse DuckDuckGo HTML into structured results, dropping any that lost their URL."""
    parser = _ResultParser()
    parser.feed(html)
    parser.close()
    usable = [r for r in parser.results if r["url"] and r["title"]]
    return usable[:max_results]


async def web_search(
    query: str,
    num_results: int = 10,
    *,
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Search the web via DuckDuckGo's HTML endpoint.

    ``user_id`` / ``workspace_id`` are accepted for call-signature parity with every other
    composite tool; this search is anonymous and workspace-independent, so neither is sent.

    Returns:
        {"status": "ok", "provider": "duckduckgo", "query": str,
         "results": [{"title": str, "url": str, "snippet": str}], "total": int}
        or {"status": "error", "error": str, "results": []}.
    """
    if not query or not query.strip():
        return {"status": "error", "error": "query is required", "results": []}

    query = query.strip()
    num_results = max(1, min(num_results, 20))
    url = _DDG_URL.format(query=quote_plus(query))

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=True, headers=_HEADERS
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except Exception as e:  # noqa: BLE001 — the tool layer needs a dict, never a raise
        logger.warning("web_search failed: %s", e)
        return {"status": "error", "error": str(e)[:200], "results": []}

    results = _parse_results(html, num_results)
    return {
        "status": "ok",
        "provider": "duckduckgo",
        "query": query,
        "results": results,
        "total": len(results),
    }
