"""Composite web search tool — searches DuckDuckGo via Playwright MCP browser.

Uses the Playwright MCP server (browser_navigate + browser_snapshot) to
perform a headless web search on DuckDuckGo's HTML endpoint. Returns
structured results without requiring any API keys.
"""

import logging
import re
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

# DuckDuckGo HTML endpoint — bot-friendly, no JavaScript needed, no CAPTCHAs
_DDG_URL = "https://html.duckduckgo.com/html/?q={query}"


async def web_search(
    query: str,
    num_results: int = 10,
    *,
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Search the web via DuckDuckGo using Playwright MCP browser.

    Navigates to DuckDuckGo HTML, takes an accessibility snapshot,
    and parses the results into structured data.

    Returns:
        {"status": "ok", "provider": "duckduckgo", "query": str,
         "results": [{"title": str, "url": str, "snippet": str}], "total": int}
    """
    if not query or not query.strip():
        return {"status": "error", "error": "query is required", "results": []}

    num_results = max(1, min(num_results, 20))

    try:
        from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool
    except ImportError:
        return {"status": "error", "error": "MCP bridge not available", "results": []}

    if not is_mcp_tool("browser_navigate"):
        return {
            "status": "error",
            "error": "Playwright MCP server not available. Ensure @playwright/mcp is installed.",
            "results": [],
        }

    url = _DDG_URL.format(query=quote_plus(query.strip()))

    try:
        # Navigate to DuckDuckGo
        await call_mcp_tool(
            "browser_navigate",
            {"url": url},
            user_id=user_id,
            workspace_id=workspace_id,
        )

        # Get accessibility snapshot of the results page
        snapshot_result = await call_mcp_tool(
            "browser_snapshot",
            {},
            user_id=user_id,
            workspace_id=workspace_id,
        )

        snapshot_text = ""
        if isinstance(snapshot_result, dict):
            snapshot_text = snapshot_result.get("content", snapshot_result.get("snapshot", ""))
            if isinstance(snapshot_text, list):
                # MCP may return content as a list of text blocks
                snapshot_text = "\n".join(
                    item.get("text", str(item)) if isinstance(item, dict) else str(item)
                    for item in snapshot_text
                )
        elif isinstance(snapshot_result, str):
            snapshot_text = snapshot_result

        results = _parse_snapshot(snapshot_text, num_results)

        return {
            "status": "ok",
            "provider": "duckduckgo",
            "query": query.strip(),
            "results": results,
            "total": len(results),
        }
    except Exception as e:
        logger.warning("web_search failed: %s", e)
        return {"status": "error", "error": str(e)[:200], "results": []}


def _parse_snapshot(snapshot_text: str, max_results: int) -> list[dict]:
    """Parse DuckDuckGo results from a Playwright accessibility snapshot.

    The snapshot contains an accessibility tree with links and text.
    DuckDuckGo HTML results appear as link elements with URLs and titles,
    followed by snippet text.
    """
    if not snapshot_text:
        return []

    results: list[dict] = []

    # Pattern: link text with URL reference — Playwright snapshots render links as:
    #   - link "Title Text" [ref=eN] -> https://url.com
    # or in some formats:
    #   link "Title Text" url="https://url.com"
    link_pattern = re.compile(
        r'link\s+"([^"]+)"\s+.*?(?:->|url="|href=")\s*(https?://[^\s"\]]+)',
        re.IGNORECASE,
    )

    lines = snapshot_text.split("\n")
    i = 0
    while i < len(lines) and len(results) < max_results:
        line = lines[i].strip()
        match = link_pattern.search(line)
        if match:
            title = match.group(1).strip()
            url = match.group(2).strip().rstrip('"')

            # Skip DuckDuckGo internal links and navigation
            if _is_result_url(url):
                # Look for snippet text in following lines
                snippet = _extract_snippet(lines, i + 1)
                results.append({"title": title, "url": url, "snippet": snippet})
        i += 1

    return results


def _is_result_url(url: str) -> bool:
    """Filter out DuckDuckGo internal/navigation URLs."""
    skip_prefixes = (
        "https://duckduckgo.com",
        "https://html.duckduckgo.com",
        "https://links.duckduckgo.com",
        "https://improving.duckduckgo.com",
        "https://help.duckduckgo.com",
        "https://about.duckduckgo.com",
        "https://spreadprivacy.com",
        "javascript:",
    )
    return not any(url.startswith(prefix) for prefix in skip_prefixes)


def _extract_snippet(lines: list[str], start_idx: int) -> str:
    """Extract snippet text from lines following a result link."""
    snippet_parts: list[str] = []
    for j in range(start_idx, min(start_idx + 5, len(lines))):
        line = lines[j].strip()
        if not line:
            continue
        # Stop at the next link or structural element
        if 'link "' in line or line.startswith("- heading") or line.startswith("- navigation"):
            break
        # Extract text content — strip accessibility annotations
        text = re.sub(r"\[ref=\w+\]", "", line)
        text = re.sub(r"^-\s*(text|paragraph|generic)\s*", "", text).strip(' "')
        if text and len(text) > 5:
            snippet_parts.append(text)
    return " ".join(snippet_parts)[:500]
