"""Browser session pool — manages concurrent Playwright sessions."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from ulid import ULID

from src.browser.interfaces import (
    BrowserSession,
    BrowserSessionPool,
)
from src.browser.playwright_session import (
    PlaywrightBrowserSession,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_SESSIONS = 3
IDLE_TIMEOUT_SECONDS = 300  # 5 minutes


class PlaywrightSessionPool(BrowserSessionPool):
    """Pool of Playwright sessions with concurrency limits.

    At most ``max_sessions`` sessions may exist at once.
    Idle sessions are cleaned up after ``idle_timeout_s``.
    """

    def __init__(
        self,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        idle_timeout_s: int = IDLE_TIMEOUT_SECONDS,
        url_allowlist: list[str] | None = None,
    ) -> None:
        self._max = max_sessions
        self._idle_timeout = idle_timeout_s
        self._url_allowlist = url_allowlist
        self._sessions: dict[str, PlaywrightBrowserSession] = {}
        self._last_used: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the idle-cleanup background loop."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._idle_cleanup_loop())

    async def stop(self) -> None:
        """Stop cleanup loop and close all sessions."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        async with self._lock:
            for sess in list(self._sessions.values()):
                await sess.close()
            self._sessions.clear()
            self._last_used.clear()

    async def acquire(self, user_id: str) -> BrowserSession:
        """Acquire or create a session for a user.

        Raises RuntimeError if the pool is full.
        """
        async with self._lock:
            # Re-use existing session for this user
            for sid, sess in self._sessions.items():
                if sess.user_id == user_id:
                    self._last_used[sid] = datetime.now(UTC)
                    return sess

            if len(self._sessions) >= self._max:
                raise RuntimeError(f"Session pool full ({self._max}). Release a session first.")

            session_id = f"bsess_{ULID()}"
            sess = PlaywrightBrowserSession(
                session_id=session_id,
                user_id=user_id,
                url_allowlist=self._url_allowlist,
            )
            self._sessions[session_id] = sess
            self._last_used[session_id] = datetime.now(UTC)
            logger.info(
                "session_acquired id=%s user=%s pool_size=%d",
                session_id,
                user_id,
                len(self._sessions),
            )
            return sess

    async def release(self, session_id: str) -> None:
        """Release and close a specific session."""
        async with self._lock:
            sess = self._sessions.pop(session_id, None)
            self._last_used.pop(session_id, None)
        if sess:
            await sess.close()
            logger.info("session_released id=%s", session_id)

    async def get_active_sessions(self) -> list[dict]:
        """Return metadata for all active sessions."""
        async with self._lock:
            return [
                {
                    "session_id": s.session_id,
                    "user_id": s.user_id,
                    "status": s.status,
                    "last_used": self._last_used.get(s.session_id),
                }
                for s in self._sessions.values()
            ]

    async def _idle_cleanup_loop(self) -> None:
        """Periodically close sessions that are idle."""
        while True:
            await asyncio.sleep(60)
            now = datetime.now(UTC)
            to_close: list[str] = []
            async with self._lock:
                for sid, ts in self._last_used.items():
                    delta = (now - ts).total_seconds()
                    if delta > self._idle_timeout:
                        to_close.append(sid)

            for sid in to_close:
                logger.info("session_idle_timeout id=%s", sid)
                await self.release(sid)
