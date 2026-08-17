"""Base class for perception connectors that read through the OpenConnector gateway.

A gateway connector knows three things: which actionId to call, how to build a
window payload from its cursor, and how to turn provider rows into RawEvents. It
knows nothing about tenants, tokens, sessions, or tool names — that is the
injected ``GatewayToolCaller``'s job (see gateway_caller.py).

This base owns the parts every gateway connector would otherwise duplicate:
MCP-error classification, the paginated walk, cursor plausibility checks, and a
health probe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from src.connectors.base import BaseConnector, ConnectorHealth
from src.connectors.poll_result import PollErrorClass, mcp_code_to_poll_class

logger = logging.getLogger(__name__)

# Overlap re-read applied to every window start. Cheap insurance against clock
# skew and second-granularity boundaries; duplicates are absorbed by
# EventProcessor's idempotency key, so the only cost is quota.
OVERLAP_SECONDS = 300

# A cursor older than this is not believable as a watermark — it is far more
# likely a stale cursor from the pre-gateway scheme (a Gmail historyId parses as
# a 1970 epoch) than a real position. Reject rather than sweep the mailbox.
CURSOR_FLOOR_DAYS = 90

# Tolerance for a cursor slightly ahead of local time (clock skew between the
# provider's timestamps and ours).
CURSOR_SKEW = timedelta(days=1)


class ToolCaller(Protocol):
    """What a gateway connector needs from its transport."""

    async def call(self, action_id: str, payload: dict) -> dict: ...


class GatewayConnector(BaseConnector):
    """Polls a provider through gateway MCP actions rather than provider REST."""

    # Cheapest read action for the health probe. Subclasses must set this.
    READ_ACTION: str = ""

    cursor_type: str = "timestamp"

    def __init__(self, settings: Any = None, caller: ToolCaller | None = None):
        super().__init__(settings=settings)
        self._caller = caller

    # ---- transport -------------------------------------------------------

    async def _call(
        self, action_id: str, payload: dict
    ) -> tuple[bool, dict, PollErrorClass | None]:
        """Invoke one action. Returns ``(ok, result, error_class)``.

        On failure ``error_class`` is set and ``result`` is empty, so a caller
        cannot mistake a failure for an empty success and advance a cursor past
        data it never received.
        """
        if self._caller is None:
            logger.warning(
                "%s has no gateway caller — poller did not inject one", type(self).__name__
            )
            return False, {}, "transient"

        envelope = await self._caller.call(action_id, payload)
        if envelope.get("status") == "ok" and not envelope.get("error"):
            result = envelope.get("result")
            return True, result if isinstance(result, dict) else {}, None

        error_class = mcp_code_to_poll_class(envelope.get("error_code"))
        logger.warning(
            "gateway action %s failed: %s (error_code=%s -> %s)",
            action_id,
            str(envelope.get("error"))[:200],
            envelope.get("error_code"),
            error_class,
        )
        return False, {}, error_class

    async def _walk_pages(
        self,
        action_id: str,
        payload: dict,
        *,
        items_key: str,
        max_pages: int,
        page_token_key: str = "pageToken",
        next_token_key: str = "nextPageToken",
    ) -> tuple[list[list[dict]], PollErrorClass | None]:
        """Follow pagination, returning one list of rows per page.

        Any page failure aborts and returns ``([], error_class)`` — never
        partial pages, because a partial walk plus an advanced cursor loses the
        rest of the window permanently.
        """
        pages: list[list[dict]] = []
        page_token: str | None = None

        for page_index in range(max_pages):
            page_payload = dict(payload)
            if page_token:
                page_payload[page_token_key] = page_token

            ok, result, error_class = await self._call(action_id, page_payload)
            if not ok:
                return [], error_class

            rows = result.get(items_key) or []
            pages.append([r for r in rows if isinstance(r, dict)])

            page_token = result.get(next_token_key)
            if not page_token:
                return pages, None

            if page_index + 1 >= max_pages:
                logger.warning(
                    "gateway action %s truncated at %d pages; the remaining window was "
                    "not drained this poll — the next poll resumes from the advanced cursor",
                    action_id,
                    max_pages,
                )

        return pages, None

    # ---- cursor plausibility --------------------------------------------

    def _sane_epoch_cursor(self, cursor: str | None) -> int | None:
        """Return an epoch-seconds cursor only if it is a believable watermark.

        Rejects the pre-gateway formats on plausibility, not parseability: a
        Gmail ``historyId`` like "1234567" is a *valid* epoch (January 1970), so
        an int() guard would accept it and the resulting ``after:1234567`` query
        would sweep the entire mailbox.
        """
        if not cursor:
            return None
        try:
            value = int(str(cursor).strip())
        except (TypeError, ValueError):
            logger.warning("discarding unparseable %s cursor %r", self.provider, cursor)
            return None

        now = datetime.now(timezone.utc)
        floor = int((now - timedelta(days=CURSOR_FLOOR_DAYS)).timestamp())
        ceiling = int((now + CURSOR_SKEW).timestamp())
        if value < floor or value > ceiling:
            logger.warning(
                "discarding implausible %s cursor %r (outside %d-day window) — "
                "falling back to an initial sync",
                self.provider,
                cursor,
                CURSOR_FLOOR_DAYS,
            )
            return None
        return value

    def _sane_rfc3339_cursor(self, cursor: str | None) -> datetime | None:
        """Return an RFC 3339 cursor only if it is a believable watermark."""
        if not cursor:
            return None
        raw = str(cursor).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            logger.warning("discarding unparseable %s cursor %r", self.provider, cursor)
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if parsed < now - timedelta(days=CURSOR_FLOOR_DAYS) or parsed > now + CURSOR_SKEW:
            logger.warning(
                "discarding implausible %s cursor %r (outside %d-day window) — "
                "falling back to an initial sync",
                self.provider,
                cursor,
                CURSOR_FLOOR_DAYS,
            )
            return None
        return parsed

    # ---- BaseConnector obligations --------------------------------------

    async def test(self, credentials: dict) -> ConnectorHealth:
        """Probe the provider with its cheapest read action."""
        ok, _, _ = await self._call(self.READ_ACTION, {})
        return ConnectorHealth(
            provider=self.provider,
            status="healthy" if ok else "down",
            last_poll_at=datetime.now(timezone.utc) if ok else None,
            error=None if ok else f"{self.READ_ACTION} failed",
        )

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        """Gateway providers do not use native OAuth.

        Native OAuth for these providers was deleted in increment 2 — the
        credential lives in OpenConnector and consent runs through the
        connection flow.
        """
        raise NotImplementedError(
            f"{type(self).__name__} is gateway-backed; connect it via "
            "POST /v1/connections/begin, not a native OAuth URL"
        )
