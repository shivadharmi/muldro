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
from collections.abc import Callable
from dataclasses import dataclass, field
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


# How much of a mismatched payload's key list may reach the log. Enough to
# diagnose ("content", "result", "ok"), bounded so a hostile or huge payload
# cannot flood the log.
KEY_LOG_LIMIT = 200


class ToolCaller(Protocol):
    """What a gateway connector needs from its transport."""

    async def call(self, action_id: str, payload: dict) -> dict: ...


@dataclass(frozen=True)
class PageWalk:
    """Outcome of one paginated walk.

    Deliberately NOT a tuple or NamedTuple. ``pages, err, _ = walk`` compiles,
    passes review, and silently drops ``truncated`` — which is the whole
    cursor-advance bug reinstated. A caller must name the field it reads.
    """

    pages: list[list[dict]] = field(default_factory=list)
    error_class: PollErrorClass | None = None
    truncated: bool = False


def _is_ok(value: Any) -> bool:
    """Normalize OpenConnector's ``ok`` flag.

    The string ``"false"`` is truthy in Python, so a bare ``not value`` guard
    reads a JSON-ish ``"ok": "false"`` as a success.
    """
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


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
        any single file, so the four hops are recorded here — with the
        confidence of each one, because they are NOT equal:

        1. **OpenConnector** answers ``{"ok": bool, ...}``.

           - ``ok`` at the root is **captured** for ``execute_action``:
             ``infra/gateway/spike-findings.md`` records a real failure body,
             ``{"ok":false,"error":{"code":"authorization_failed", ...}}``.
             Note that a failure nests its code under ``error.code`` — there is
             no top-level ``error_code`` in that capture.
           - The **success** shape ``{"ok": true, "data": {<provider payload>}}``
             is **INFERRED, never captured**. The evidence is a *different tool*:
             ``infra/gateway/spike-findings-guide.md`` (title: "OpenConnector
             v1.3.5 ``get_action_guide``") records
             ``result.structuredContent.data.*`` for a **guide** response, and
             the per-action ``outputSchema`` in
             ``tests/fixtures/openconnector_curated_schemas.json`` describes
             what sits at the ``data`` level (``messages``/``items``). No
             successful ``execute_action`` body exists in this repo. **Live
             acceptance is the step that would confirm it** — until then treat
             the ``data`` nesting as a hypothesis, and do NOT delete the guards
             below as redundant.
        2. **The adapter passes it through** — ``src/adapter/server.py``
           ``handle_execute_action`` returns ``strip_secrets(_result_to_dict(result))``,
           and ``_result_to_dict`` prefers ``structured_content`` /
           ``structuredContent`` / ``data`` when they are dicts, then falls back
           to ``{"content": [...]}`` and ``{"result": str(...)}``. Which branch
           fires depends on whether OpenConnector sets ``structuredContent`` on
           ``execute_action`` results — also unverified.
        3. **FastMCP serializes** that tool return (registered in
           ``run_adapter.py`` and ``src/adapter/warm_start.py``) into a **text**
           content block.
        4. **The session pool stringifies** — ``src/integrations/session_pool.py``
           ``call_tool`` joins the text blocks and returns
           ``{"status": "ok", "result": <str>}``; ``call_mcp_tool``
           (``src/connectors/mcp_bridge.py``) passes that dict through unchanged.

        So ``envelope["result"]`` arrives as a **JSON string**. Because hop 1's
        success shape is unconfirmed, this method deliberately does NOT try to
        recognise a provider payload — it cannot, having no idea what one looks
        like. It only rules out what is definitely wrong (a ``data`` key of the
        wrong type) and passes a root-level dict through. The shapes it cannot
        judge — ``{"content": ...}``, ``{"result": ...}``, ``{"ok": true}`` — are
        caught one level up by ``_walk_pages``, which holds ``items_key`` and so
        DOES know what a payload must contain.
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
        if "ok" in parsed and not _is_ok(parsed["ok"]):
            # The one captured OC failure body (infra/gateway/spike-findings.md)
            # nests BOTH the code and the message under "error":
            #   {"ok":false,"error":{"code":"authorization_failed",
            #    "message":"Configure github credentials first.", ...}}
            # Reading a top-level "error_code" therefore yields None for every
            # real action failure, and mcp_code_to_poll_class(None) collapses
            # rate limits and validation errors into "transient" (threshold 6).
            # The flat form is still honoured — nothing forbids it.
            raw_error = parsed.get("error")
            nested = raw_error if isinstance(raw_error, dict) else None
            code = (nested.get("code") if nested else None) or parsed.get("error_code")
            detail = (nested.get("message") if nested else raw_error) or parsed.get("message")
            error_class = mcp_code_to_poll_class(code)
            logger.warning(
                "gateway action %s reported ok=false: %s (error_code=%s -> %s)",
                action_id,
                str(detail)[:200],
                code,
                error_class,
            )
            return False, {}, error_class

        # A `data` key that is present but not a dict is a shape MISMATCH, not
        # an alternative shape: the rows live inside it, so passing the parsed
        # envelope back instead would hand the caller a payload with no rows
        # and no error — the empty-success failure mode this class exists to
        # prevent. Only the no-`data`-key case falls through, and it is
        # backstopped by _walk_pages' items_key check.
        if "data" in parsed:
            data = parsed["data"]
            if not isinstance(data, dict):
                logger.warning(
                    "gateway action %s returned a %s under 'data', not an object — "
                    "refusing to treat it as an empty window",
                    action_id,
                    type(data).__name__,
                )
                return False, {}, "transient"
            return True, data, None

        return True, parsed, None

    async def _walk_pages(
        self,
        action_id: str,
        payload: dict,
        *,
        items_key: str,
        max_pages: int,
        page_token_key: str = "pageToken",
        next_token_key: str = "nextPageToken",
        stop_when: Callable[[list[dict]], bool] | None = None,
    ) -> PageWalk:
        """Follow pagination, returning a :class:`PageWalk`.

        ``items_key``/``next_token_key`` address the **unwrapped provider
        payload** — what ``_call`` returns after stripping OpenConnector's
        ``{ok, data}`` envelope. See the recorded ``outputSchema`` in
        ``tests/fixtures/openconnector_curated_schemas.json`` for the real key
        names (gmail: ``messages``; calendar: ``items``).

        **An absent ``items_key`` is a FAILURE, not an empty page.** This is
        where the payload-vs-artifact ambiguity ``_call`` cannot resolve gets
        resolved, because only here is the expected key known. The recorded
        ``outputSchema`` entries put ``items_key`` in their ``required`` list
        (``gmail.fetch_emails`` -> ``required: ["messages"]``;
        ``googlecalendar.list_events`` -> ``required: ["items"]``) and type it
        ``array`` — so OpenConnector declares the key ALWAYS present on a
        success. Its absence therefore cannot mean "no mail"; it means we are
        looking at the wrong object (an adapter ``{"content": ...}`` /
        ``{"result": ...}`` artifact, or a bare ``{"ok": true}``). Do not relax
        this to ``result.get(items_key) or []`` — that collapses "key absent"
        into "empty page" and makes the caller advance its cursor past a window
        it never read.

        Any page failure aborts and returns no pages — never partial pages,
        because a partial walk plus an advanced cursor loses the rest of the
        window permanently.

        ``truncated`` is True when the walk stopped at ``max_pages`` while the
        provider was still offering a next-page token. It is a field rather
        than only a log line because the two outcomes are otherwise
        indistinguishable to a caller.

        **Consuming policy for subclasses: resolve the next cursor through
        :meth:`_resolve_cursor`, never by reading ``truncated`` inline.** That
        method owns the "never advance past an undrained window" rule so it
        lives in one place; every subclass must route through it (an explicit
        per-connector acceptance criterion).

        ``stop_when`` ends the walk cleanly once a page proves the rest is
        already known. It exists for providers whose read action offers no
        server-side time window: those must sort NEWEST-FIRST and stop at the
        watermark, because the alternative — oldest-first with a client-side
        skip — re-reads the whole history every poll and, once the history
        exceeds ``max_pages``, truncates before ever reaching the new rows. The
        cursor is then held (correctly) and the walk repeats the same pages for
        ever, observing nothing. A stop is emphatically NOT truncation: the
        window WAS drained, so ``truncated`` stays False and the caller may
        advance. Returning True on a page that does not actually prove
        exhaustion would advance the cursor past unread rows.
        """
        pages: list[list[dict]] = []
        page_token: str | None = None

        for _page_index in range(max_pages):
            page_payload = dict(payload)
            if page_token:
                page_payload[page_token_key] = page_token

            ok, result, error_class = await self._call(action_id, page_payload)
            if not ok:
                return PageWalk(pages=[], error_class=error_class, truncated=False)

            if items_key not in result:
                logger.warning(
                    "gateway action %s returned no %r key — the recorded outputSchema "
                    "lists it as required, so this is a shape mismatch, not an empty "
                    "page; keys present: %s",
                    action_id,
                    items_key,
                    str(sorted(result))[:KEY_LOG_LIMIT],
                )
                return PageWalk(pages=[], error_class="transient", truncated=False)

            # A `None` here falls through to the type guard BY DESIGN, and that
            # design was reversed once. The earlier reasoning — "coerce null to
            # [] so a provider using null-for-empty does not circuit-break every
            # empty poll" — loses to the schema and to the failure shape: the
            # recorded outputSchema marks `messages`/`items` `required` AND
            # types them `array`, so `null` is not a valid empty, it is a
            # mismatch; and a mid-walk null is indistinguishable from a
            # truncated page, which is precisely the silent-loss shape (page 1
            # rows + nextPageToken, page 2 null -> walk ends "cleanly", cursor
            # advances past an undrained window). If a provider genuinely
            # answers null-for-empty we will see loud `transient` failures and
            # can revisit WITH EVIDENCE, which beats losing mail quietly.
            rows = result[items_key]
            if not isinstance(rows, list):
                logger.warning(
                    "gateway action %s returned a %s under %r; the recorded outputSchema "
                    "types it as an array, so this is a shape mismatch, not an empty page",
                    action_id,
                    type(rows).__name__,
                    items_key,
                )
                return PageWalk(pages=[], error_class="transient", truncated=False)

            kept = [r for r in rows if isinstance(r, dict)]
            if len(kept) != len(rows):
                # Non-dict rows cannot become RawEvents, but dropping them
                # without a word is the same defect class as an empty success.
                logger.warning(
                    "gateway action %s: dropped %d non-object row(s) from a page of %d",
                    action_id,
                    len(rows) - len(kept),
                    len(rows),
                )
            pages.append(kept)

            if stop_when is not None and stop_when(kept):
                return PageWalk(pages=pages, error_class=None, truncated=False)

            page_token = result.get(next_token_key)
            if not page_token:
                return PageWalk(pages=pages, error_class=None, truncated=False)

        logger.warning(
            "gateway action %s truncated at %d pages; the remaining window was not "
            "drained this poll — the caller must not advance its cursor",
            action_id,
            max_pages,
        )
        return PageWalk(pages=pages, error_class=None, truncated=True)

    def _resolve_cursor(
        self, walk: PageWalk, *, incoming: str | None, observed: str | None
    ) -> str | None:
        """Never advance past an undrained window.

        On truncation the remaining window was not read, so advancing would
        skip it permanently; and with nothing observed there is nothing to
        advance to (jumping to now() would skip anything delivered since the
        last row). Re-polling the same window is cheap — duplicates are
        absorbed by EventProcessor's idempotency key — while losing the tail is
        not recoverable.

        The candidate cursor is passed IN rather than derived here on purpose:
        ``observed`` is the maximum watermark seen *during* the walk, so it
        cannot exist before the walk runs, and the connectors' watermarks are
        different types (Gmail an epoch-seconds int rendered as a string,
        Calendar an RFC 3339 string) — a single generic comparison across them
        is not obviously safe. Each subclass computes its own max; this method
        owns only the hold-or-advance decision.
        """
        if walk.truncated or observed is None:
            return incoming
        return observed

    # ---- cursor plausibility --------------------------------------------

    def _epoch_cursor_ceiling(self) -> int:
        """The single upper bound an epoch-seconds watermark may hold.

        The READ side (:meth:`_sane_epoch_cursor`) and the WRITE side (a
        subclass folding a provider timestamp into its watermark) must share
        this bound, which is why it is a method rather than a constant repeated
        at each site. If the two ever disagree, a stamp the writer accepts and
        the reader rejects PINS the cursor: written -> rejected on read ->
        initial window -> the same row re-observed -> the same value written
        again, for ever, with no error and no log.

        Provider timestamps are sender-controlled (Gmail's ``messageTimestamp``
        derives from the ``Date`` header), so a future stamp is routine input,
        not a corruption.
        """
        return int((datetime.now(timezone.utc) + CURSOR_SKEW).timestamp())

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
        ceiling = self._epoch_cursor_ceiling()
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
