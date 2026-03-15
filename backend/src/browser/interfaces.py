"""Browser automation interfaces — design only, no implementation.

These interfaces define the contract for future browser automation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PageState:
    url: str
    title: str
    status: str  # loaded, loading, error


@dataclass
class ActionResult:
    success: bool
    error: str | None = None
    screenshot_ref: str | None = None


@dataclass
class BrowserAction:
    action_type: str  # navigate, click, fill, screenshot, extract
    selector: str | None = None
    value: str | None = None
    url: str | None = None
    result_status: str = "pending"
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    created_at: datetime = field(default_factory=datetime.now)


class BrowserSession(ABC):
    """Abstract browser session for future implementation."""

    session_id: str
    user_id: str
    status: str  # idle, active, recording

    @abstractmethod
    async def navigate(self, url: str) -> PageState:
        """Navigate to a URL."""

    @abstractmethod
    async def click(self, selector: str) -> ActionResult:
        """Click an element."""

    @abstractmethod
    async def fill(self, selector: str, value: str) -> ActionResult:
        """Fill a form field."""

    @abstractmethod
    async def screenshot(self) -> bytes:
        """Take a screenshot, return as bytes."""

    @abstractmethod
    async def extract_text(self, selector: str | None = None) -> str:
        """Extract text from the page or a specific element."""

    @abstractmethod
    async def close(self) -> None:
        """Close the browser session."""


class BrowserSessionPool(ABC):
    """Manages browser session lifecycle."""

    @abstractmethod
    async def acquire(self, user_id: str) -> BrowserSession:
        """Acquire a browser session for a user."""

    @abstractmethod
    async def release(self, session_id: str) -> None:
        """Release a browser session."""

    @abstractmethod
    async def get_active_sessions(self) -> list[dict]:
        """List active browser sessions."""


@dataclass
class BrowserActionLog:
    """Immutable log of browser actions for replay/audit."""

    log_id: str
    session_id: str
    actions: list[BrowserAction] = field(default_factory=list)
    screenshot_artifact_ids: list[str] = field(default_factory=list)
