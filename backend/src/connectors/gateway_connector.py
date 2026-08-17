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

import json
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
        # Fail at wiring time, not at probe time. ``test()`` promises a
        # ConnectorHealth return; with an empty READ_ACTION it would instead
        # raise out of action_id_to_tool_name deep inside the transport.
        if not self.READ_ACTION:
            raise ValueError(
                f"{type(self).__name__} must set READ_ACTION — test() probes the provider "
                "with it, and an empty action id raises inside the transport instead of "
                "returning ConnectorHealth(status='down')"
            )
        self._caller = caller

    # ---- transport -------------------------------------------------------

    async def _call(
        self, action_id: str, payload: dict
    ) -> tuple[bool, dict, PollErrorClass | None]:
        """Invoke one action. Returns ``(ok, result, error_class)``.

        On failure ``error_class`` is set and ``result`` is empty. ``{}`` is
        never a substitute for "I could not read this": every unparseable,
        non-dict or ``ok: false`` response is returned as a FAILURE, so a
        subclass cannot mistake it for an empty window and advance its cursor
        past data it never received.

        **The envelope chain this unwraps.** The shape is not discoverable from
        any single file, so the four hops are recorded here:

        1. **OpenConnector** answers ``{"ok": bool, "data": {<provider payload>}}``
           — ``infra/gateway/spike-findings-guide.md`` records
           ``result.structuredContent.ok -> bool`` and
           ``result.structuredContent.data.* -> object``. The per-action
           ``outputSchema`` in ``tests/fixtures/openconnector_curated_schemas.json``
           describes the ``data`` level (``messages``/``items`` sit at its top).
        2. **The adapter passes it through** — ``src/adapter/server.py``
           ``handle_execute_action`` returns ``strip_secrets(_result_to_dict(result))``,
           and ``_result_to_dict`` prefers ``structured_content`` /
           ``structuredContent`` / ``data`` when they are dicts.
        3. **FastMCP serializes** that ``-> dict`` tool return (registered in
           ``run_adapter.py`` and ``src/adapter/warm_start.py``) into a **text**
           content block.
        4. **The session pool stringifies** — ``src/integrations/session_pool.py``
           ``call_tool`` joins the text blocks and returns
           ``{"status": "ok", "result": <str>}``; ``call_mcp_tool``
           (``src/connectors/mcp_bridge.py``) passes that dict through unchanged.

        So ``envelope["result"]`` arrives as a **JSON string** and the provider
        payload sits one level down under ``data``. The dict branch and the
        no-``data`` fallback below are deliberate: ``_result_to_dict`` also has
        ``{"content": [...]}`` and ``{"result": str(...)}`` branches, so a
        payload that is not the ``{ok, data}`` shape can legitimately arrive.
        """
        if self._caller is None:
            logger.warning(
                "%s has no gateway caller — poller did not inject one", type(self).__name__
            )
            return False, {}, "transient"

        envelope = await self._caller.call(action_id, payload)
        if envelope.get("status") != "ok" or envelope.get("error"):
            error_class = mcp_code_to_poll_class(envelope.get("error_code"))
            # make_error_response (src/integrations/mcp_errors.py) and the
            # circuit-open envelope (session_pool.py) both report under
            # "message"; the auth_required and bridge envelopes use "error".
            # Read both, or the most common failures log "failed: None".
            detail = envelope.get("error") or envelope.get("message")
            logger.warning(
                "gateway action %s failed: %s (error_code=%s -> %s)",
                action_id,
                str(detail)[:200],
                envelope.get("error_code"),
                error_class,
            )
            return False, {}, error_class

        raw = envelope.get("result")
        if isinstance(raw, str):
            try:
                parsed: Any = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "gateway action %s returned unparseable JSON (%s); first 200 chars: %r",
                    action_id,
                    exc,
                    raw[:200],
                )
                return False, {}, "transient"
        elif isinstance(raw, dict):
            parsed = raw
        else:
            logger.warning(
                "gateway action %s returned a %s result, not a JSON string or dict",
                action_id,
                type(raw).__name__,
            )
            return False, {}, "transient"

        if not isinstance(parsed, dict):
            logger.warning(
                "gateway action %s payload parsed to %s, not an object",
                action_id,
                type(parsed).__name__,
            )
            return False, {}, "transient"

        # OpenConnector reports action-level failure INSIDE a transport-level
        # success, so this is the only place it can be caught.
        if "ok" in parsed and not parsed["ok"]:
            error_class = mcp_code_to_poll_class(parsed.get("error_code"))
            logger.warning(
                "gateway action %s reported ok=false: %s (error_code=%s -> %s)",
                action_id,
                str(parsed.get("error") or parsed.get("message"))[:200],
                parsed.get("error_code"),
                error_class,
            )
            return False, {}, error_class

        data = parsed.get("data")
        return True, data if isinstance(data, dict) else parsed, None

    async def _walk_pages(
        self,
        action_id: str,
        payload: dict,
        *,
        items_key: str,
        max_pages: int,
        page_token_key: str = "pageToken",
        next_token_key: str = "nextPageToken",
    ) -> tuple[list[list[dict]], PollErrorClass | None, bool]:
        """Follow pagination, returning ``(pages, error_class, truncated)``.

        ``items_key``/``next_token_key`` address the **unwrapped provider
        payload** — what ``_call`` returns after stripping OpenConnector's
        ``{ok, data}`` envelope. See the recorded ``outputSchema`` in
        ``tests/fixtures/openconnector_curated_schemas.json`` for the real key
        names (gmail: ``messages``; calendar: ``items``).

        Any page failure aborts and returns ``([], error_class, False)`` — never
        partial pages, because a partial walk plus an advanced cursor loses the
        rest of the window permanently.

        ``truncated`` is True when the walk stopped at ``max_pages`` while the
        provider was still offering a next-page token. It is a return value
        rather than only a log line because the two outcomes are otherwise
        indistinguishable to a caller.

        **Consuming policy for subclasses: on ``truncated`` do NOT advance the
        cursor.** The window was not drained; advancing would skip the
        remainder permanently. Re-polling the same window is cheap (duplicates
        are absorbed by EventProcessor's idempotency key); losing the tail is
        not recoverable.
        """
        pages: list[list[dict]] = []
        page_token: str | None = None

        for _page_index in range(max_pages):
            page_payload = dict(payload)
            if page_token:
                page_payload[page_token_key] = page_token

            ok, result, error_class = await self._call(action_id, page_payload)
            if not ok:
                return [], error_class, False

            rows = result.get(items_key) or []
            pages.append([r for r in rows if isinstance(r, dict)])

            page_token = result.get(next_token_key)
            if not page_token:
                return pages, None, False

        logger.warning(
            "gateway action %s truncated at %d pages; the remaining window was not "
            "drained this poll — the caller must not advance its cursor",
            action_id,
            max_pages,
        )
        return pages, None, True

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
