"""Browser tool functions for agent use.

Each function is a plain async callable that operates on a
PlaywrightSessionPool. These are intended to be registered
as MCP tools in the intelligence server.
"""

from __future__ import annotations

import base64
import logging

from src.browser.interfaces import PageState
from src.browser.session_pool import (
    PlaywrightSessionPool,
)

logger = logging.getLogger(__name__)


async def browser_open(
    pool: PlaywrightSessionPool,
    user_id: str,
    url: str,
) -> dict:
    """Open a URL in a browser session.

    Returns page state dict with url, title, status.
    """
    session = await pool.acquire(user_id)
    page: PageState = await session.navigate(url)
    return {
        "session_id": session.session_id,
        "url": page.url,
        "title": page.title,
        "status": page.status,
    }


async def browser_snapshot(
    pool: PlaywrightSessionPool,
    user_id: str,
) -> dict:
    """Return current page state without navigating."""
    session = await pool.acquire(user_id)
    if session._page is None:  # noqa: SLF001
        return {
            "session_id": session.session_id,
            "url": None,
            "title": None,
            "status": "idle",
        }
    url = session._page.url  # noqa: SLF001
    title = await session._page.title()  # noqa: SLF001
    return {
        "session_id": session.session_id,
        "url": url,
        "title": title,
        "status": "loaded",
    }


async def browser_extract(
    pool: PlaywrightSessionPool,
    user_id: str,
    selector: str | None = None,
) -> dict:
    """Extract text from the current page or element."""
    session = await pool.acquire(user_id)
    text = await session.extract_text(selector)
    return {
        "session_id": session.session_id,
        "text": text[:5000],  # cap output size
        "truncated": len(text) > 5000,
    }


async def browser_click(
    pool: PlaywrightSessionPool,
    user_id: str,
    selector: str,
) -> dict:
    """Click an element on the current page."""
    session = await pool.acquire(user_id)
    result = await session.click(selector)
    return {
        "session_id": session.session_id,
        "success": result.success,
        "error": result.error,
    }


async def browser_type(
    pool: PlaywrightSessionPool,
    user_id: str,
    selector: str,
    value: str,
) -> dict:
    """Type text into a form field."""
    session = await pool.acquire(user_id)
    result = await session.fill(selector, value)
    return {
        "session_id": session.session_id,
        "success": result.success,
        "error": result.error,
    }


async def browser_submit(
    pool: PlaywrightSessionPool,
    user_id: str,
    selector: str = "form",
) -> dict:
    """Submit a form by clicking its submit button.

    Clicks the first submit-type input/button within
    the given form selector.
    """
    session = await pool.acquire(user_id)
    submit_sel = f"{selector} [type=submit], {selector} button:not([type=button])"
    result = await session.click(submit_sel)
    return {
        "session_id": session.session_id,
        "success": result.success,
        "error": result.error,
    }


async def browser_screenshot(
    pool: PlaywrightSessionPool,
    user_id: str,
) -> dict:
    """Take a screenshot, returned as base64 PNG."""
    session = await pool.acquire(user_id)
    data = await session.screenshot()
    if not data:
        return {
            "session_id": session.session_id,
            "image_base64": None,
            "error": "Screenshot failed",
        }
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "session_id": session.session_id,
        "image_base64": encoded,
        "size_bytes": len(data),
    }
