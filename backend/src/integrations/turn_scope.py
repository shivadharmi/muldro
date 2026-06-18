"""Per-turn MCP session lifecycle scope.

A TurnScope tracks which MCP session keys were opened (or reused) during a
single agent turn so they can be torn down when the turn ends. Sessions are
reference-counted: opening or reusing a key increments its count, and at turn
end every key the scope still holds is handed to ``on_close`` for teardown.
Reference counting lets overlapping turns share a session without one turn
killing another's live connection.

The active scope is stored in a ContextVar. Synchronous, awaited work within
the turn (including nested ``async with``/``await`` calls) sees the scope and
is tracked correctly. IMPORTANT: code spawned as a *detached* background task
(e.g. ``asyncio.create_task`` / ``_spawn_background``) that OUTLIVES the turn
must NOT open turn-scoped MCP sessions — ``on_close`` fires when the turn's
``async with`` exits, which may precede the background task's execution, so any
session it opens would not be torn down by this turn (it would be reclaimed
only by the idle reaper). Keep MCP tool calls on the awaited turn path.
"""

from __future__ import annotations

import contextvars
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

logger = logging.getLogger(__name__)

SessionKey = tuple[str, str, str]  # (workspace_id, server_name, user_id)

_current: contextvars.ContextVar["TurnScope | None"] = contextvars.ContextVar(
    "mcp_turn_scope", default=None
)


class TurnScope:
    """Tracks reference-counted MCP session keys opened during one turn."""

    def __init__(self) -> None:
        self._refs: dict[SessionKey, int] = defaultdict(int)

    def register(self, key: SessionKey) -> None:
        """Record a newly opened session key."""
        self._refs[key] += 1

    def acquire(self, key: SessionKey) -> None:
        """Record reuse of an already-open session key within this turn."""
        self._refs[key] += 1

    def release_one(self, key: SessionKey) -> None:
        """Manually drop one reference (rarely needed; mid-turn refresh)."""
        if self._refs.get(key, 0) > 0:
            self._refs[key] -= 1

    def refcount(self, key: SessionKey) -> int:
        return self._refs.get(key, 0)

    def keys(self) -> list[SessionKey]:
        return [k for k, n in self._refs.items() if n > 0]


def current_turn_scope() -> TurnScope | None:
    """Return the TurnScope active for the current task, if any."""
    return _current.get()


@asynccontextmanager
async def turn_scope(
    *,
    on_close: Callable[[list[SessionKey]], object] | None = None,
) -> AsyncIterator[TurnScope]:
    """Activate a TurnScope for the duration of an agent turn.

    On exit, hands every still-held session key to ``on_close`` for teardown.
    ``on_close`` may be sync or async; both are awaited if awaitable.
    """
    scope = TurnScope()
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)
        keys = scope.keys()
        if on_close is not None:
            try:
                result = on_close(keys)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                logger.warning("TurnScope on_close failed", exc_info=True)
