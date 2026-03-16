"""Playwright-backed browser session implementation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from ulid import ULID

from src.browser.interfaces import (
    ActionResult,
    BrowserSession,
    PageState,
)

logger = logging.getLogger(__name__)

# Safety: only allow navigating to these domain patterns.
DEFAULT_URL_ALLOWLIST = [
    "https://*.google.com",
    "https://*.github.com",
    "https://*.slack.com",
    "https://*.notion.so",
    "https://*.linear.app",
    "https://*.atlassian.net",
    "https://localhost:*",
    "http://localhost:*",
]


def _url_matches_allowlist(url: str, allowlist: list[str]) -> bool:
    """Check if a URL matches any pattern in the allowlist."""
    import fnmatch
    from urllib.parse import urlparse

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for pattern in allowlist:
        if fnmatch.fnmatch(origin, pattern):
            return True
    return False


class PlaywrightBrowserSession(BrowserSession):
    """Playwright-backed browser session with safety controls."""

    def __init__(
        self,
        session_id: str,
        user_id: str,
        url_allowlist: list[str] | None = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.status = "idle"
        self._url_allowlist = url_allowlist or DEFAULT_URL_ALLOWLIST
        self._page = None
        self._browser = None
        self._context = None
        self._actions: list[dict] = []

    async def _ensure_browser(self) -> None:
        """Lazily start browser and page."""
        if self._page is not None:
            return
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        self._browser = await pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=("Jarvis-Browser/1.0 (+https://github.com/jarvis)"),
        )
        self._page = await self._context.new_page()
        self.status = "active"

    def _log_action(
        self,
        action_type: str,
        *,
        selector: str | None = None,
        value: str | None = None,
        result_status: str = "success",
        error: str | None = None,
    ) -> dict:
        """Record an action for audit trail."""
        entry = {
            "action_id": f"bact_{ULID()}",
            "session_id": self.session_id,
            "action_type": action_type,
            "selector": selector,
            "value": value,
            "result_status": result_status,
            "error": error,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._actions.append(entry)
        logger.info(
            "browser_action session=%s type=%s status=%s",
            self.session_id,
            action_type,
            result_status,
        )
        return entry

    @property
    def action_log(self) -> list[dict]:
        """Return recorded actions for persistence."""
        return list(self._actions)

    async def navigate(self, url: str) -> PageState:
        """Navigate to a URL (must be on allowlist)."""
        if not _url_matches_allowlist(url, self._url_allowlist):
            self._log_action(
                "navigate",
                value=url,
                result_status="failed",
                error="URL not on allowlist",
            )
            return PageState(
                url=url,
                title="",
                status="error",
            )

        await self._ensure_browser()
        assert self._page is not None
        try:
            resp = await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = "loaded" if resp and resp.ok else "error"
            title = await self._page.title()
            self._log_action("navigate", value=url, result_status="success")
            return PageState(
                url=self._page.url,
                title=title,
                status=status,
            )
        except Exception as exc:
            self._log_action(
                "navigate",
                value=url,
                result_status="failed",
                error=str(exc)[:200],
            )
            return PageState(url=url, title="", status="error")

    async def click(self, selector: str) -> ActionResult:
        """Click an element by CSS selector."""
        await self._ensure_browser()
        assert self._page is not None
        try:
            await self._page.click(selector, timeout=10000)
            self._log_action("click", selector=selector)
            return ActionResult(success=True)
        except Exception as exc:
            err = str(exc)[:200]
            self._log_action(
                "click",
                selector=selector,
                result_status="failed",
                error=err,
            )
            return ActionResult(success=False, error=err)

    async def fill(self, selector: str, value: str) -> ActionResult:
        """Fill a form field."""
        await self._ensure_browser()
        assert self._page is not None
        try:
            await self._page.fill(selector, value, timeout=10000)
            self._log_action(
                "fill",
                selector=selector,
                # Do not log value — may contain secrets
                value="[redacted]",
            )
            return ActionResult(success=True)
        except Exception as exc:
            err = str(exc)[:200]
            self._log_action(
                "fill",
                selector=selector,
                result_status="failed",
                error=err,
            )
            return ActionResult(success=False, error=err)

    async def screenshot(self) -> bytes:
        """Capture a full-page screenshot as PNG bytes."""
        await self._ensure_browser()
        assert self._page is not None
        try:
            data = await self._page.screenshot(type="png", full_page=True)
            self._log_action("screenshot")
            return data
        except Exception as exc:
            err = str(exc)[:200]
            self._log_action(
                "screenshot",
                result_status="failed",
                error=err,
            )
            return b""

    async def extract_text(self, selector: str | None = None) -> str:
        """Extract visible text from page or element."""
        await self._ensure_browser()
        assert self._page is not None
        try:
            if selector:
                el = await self._page.query_selector(selector)
                text = await el.inner_text() if el else ""
            else:
                text = await self._page.inner_text("body")
            self._log_action("extract", selector=selector)
            return text
        except Exception as exc:
            err = str(exc)[:200]
            self._log_action(
                "extract",
                selector=selector,
                result_status="failed",
                error=err,
            )
            return ""

    async def close(self) -> None:
        """Close browser and release resources."""
        self.status = "idle"
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
        except Exception:
            logger.exception(
                "Error closing session %s",
                self.session_id,
            )
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._log_action("close")
