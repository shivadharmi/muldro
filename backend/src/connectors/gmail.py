"""Gmail perception, read through the OpenConnector gateway.

Native OAuth was retired in increment 2, so the provider REST transport this
connector used to drive is gone: the credential now lives in OpenConnector and
every read goes through one gateway action, ``gmail.fetch_emails``.

Three decisions here are settled; do not re-litigate them from the code alone.

**The message DTO is OpenConnector's, not Gmail's.** OC reshapes each message.
The recorded ``outputSchema`` (``tests/fixtures/openconnector_curated_schemas.json``)
marks ``messageId``, ``threadId``, ``labelIds``, ``subject``, ``sender``, ``to``
and ``messageTimestamp`` required, and offers ``preview`` (object), ``payload``
(an opaque, nullable Gmail passthrough), ``messageText``, ``attachmentList`` and
``raw``. It does NOT promise ``id``, ``internalDate`` or ``snippet`` — only
``additionalProperties: true`` allows them through. Reading ``msg["id"]``
therefore skips every real message and emits zero events, which looks exactly
like an empty mailbox. Every read below prefers OC's name and falls back to the
native one, never the reverse.

**``detail="full"``.** The alternative was ``detail="ids"`` plus a per-message
``gmail.get_message``, but that action's DTO is strictly poorer —
``additionalProperties: false``, seven flat strings, **no headers and no
labelIds** — so it cannot carry ``List-Unsubscribe`` / ``List-Id`` /
``Precedence``, which ``services/triage.classify_by_rules`` uses for its
LLM-free newsletter pre-pass. ``full`` is the only level that can. (If a
per-message call were ever needed it would be ``gmail.fetch_message_by_message_id``.)

**The cursor is a timestamp, not a historyId.** ``gmail.list_history`` exists,
but the native connector already narrowed to ``historyTypes=messageAdded``, so
history's extra fidelity was discarded at the filter anyway. A timestamp cannot
expire, which deletes the 404-resync recursion entirely. The watermark is the
max observed message timestamp in epoch **seconds**, stored as a decimal string;
each incremental window re-reads ``OVERLAP_SECONDS`` of already-seen mail, whose
duplicates EventProcessor's idempotency key absorbs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from src.connectors.base import register_connector
from src.connectors.gateway_connector import OVERLAP_SECONDS, GatewayConnector
from src.connectors.poll_result import PollResult
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

# Pages walked on a first sync. Bounds the backfill so a brand-new connection
# cannot fan out unboundedly.
MAX_BACKFILL_PAGES = 4

# Pages walked on an incremental sync. A window bounded by the previous
# watermark cannot legitimately span many pages, so this is a defensive cap on a
# provider that keeps handing back a nextPageToken — not a throughput knob.
MAX_INCREMENTAL_PAGES = 10

PAGE_SIZE = 25

# The first sync's window, unchanged from the pre-gateway connector.
INITIAL_QUERY = "is:inbox newer_than:3d"

# Bulk-mail signal headers, captured separately from the rest so triage's
# deterministic pre-pass can skip marketing mail without an LLM call. Matched
# case-insensitively; the captured keys keep their original casing.
BULK_MAIL_HEADERS = frozenset({"list-unsubscribe", "list-id", "precedence"})

# Above this magnitude an integer epoch can only be milliseconds: 1e11 seconds
# is the year 5138, while 1e11 milliseconds is 1973.
_EPOCH_MILLIS_FLOOR = 10**11

SUMMARY_MAX_CHARS = 500


def _first_text(source: dict, *keys: str) -> str:
    """First non-blank string among ``keys``, or ``""``."""
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _headers_of(message: dict) -> dict[str, str]:
    """Flatten ``payload.headers`` into a name -> value map.

    ``payload`` is an opaque passthrough (``additionalProperties: true``,
    nullable), so headers are NOT schema-guaranteed. Every shape that is not the
    expected list-of-``{name, value}`` degrades to an empty map rather than
    raising: a message with no headers is still a message worth emitting.
    """
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("headers")
    if not isinstance(raw, list):
        return {}
    headers: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = entry.get("value")
        if isinstance(name, str) and value is not None:
            headers[name] = str(value)
    return headers


def _from_epoch(value: object, *, millis: bool = False) -> datetime | None:
    """Parse an integer epoch. ``millis`` forces ms (Gmail's ``internalDate``)."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    seconds = number / 1000 if millis or abs(number) >= _EPOCH_MILLIS_FLOOR else float(number)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _from_text_timestamp(value: object) -> datetime | None:
    """Parse an ISO 8601 or RFC 2822 timestamp into an aware UTC datetime.

    RFC 2822 is covered because the schema leaves ``messageTimestamp``'s format
    unspecified and Gmail's own ``Date`` header uses it. If that form did not
    parse, every message would carry ``occurred_at=None``, nothing would ever be
    observed, and the cursor would silently never advance.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _occurred_at(message: dict) -> datetime | None:
    """Best available send time, or ``None`` — never a fabricated ``now()``.

    A fabricated timestamp would poison the watermark: it would advance the
    cursor past mail that was never actually read.
    """
    native = _from_epoch(message.get("internalDate"), millis=True)
    if native is not None:
        return native
    stamp = message.get("messageTimestamp")
    return _from_epoch(stamp) or _from_text_timestamp(stamp)


def _snippet_of(message: dict) -> str:
    """Short body preview: native ``snippet``, else ``preview``, else body text."""
    native = _first_text(message, "snippet")
    if native:
        return native
    preview = message.get("preview")
    if isinstance(preview, str) and preview.strip():
        return preview
    if isinstance(preview, dict):
        # The schema types `preview` as an opaque object, so probe the plausible
        # text keys rather than assuming one.
        text = _first_text(preview, "snippet", "text", "body", "preview")
        if text:
            return text
    return _first_text(message, "messageText")


@register_connector("gmail")
class GmailConnector(GatewayConnector):
    """Polls Gmail through OpenConnector, windowed on a timestamp watermark."""

    cursor_type: str = "timestamp"
    READ_ACTION = "gmail.get_profile"
    FETCH_ACTION = "gmail.fetch_emails"

    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Read one window of inbox mail. ``credentials`` is unused — the
        gateway holds the credential; the caller carries the tenant identity."""
        watermark = self._sane_epoch_cursor(cursor)
        if watermark is None:
            query = INITIAL_QUERY
            max_pages = MAX_BACKFILL_PAGES
        else:
            query = f"after:{watermark - OVERLAP_SECONDS} is:inbox"
            max_pages = MAX_INCREMENTAL_PAGES

        payload = {"query": query, "detail": "full", "maxResults": PAGE_SIZE}
        walk = await self._walk_pages(
            self.FETCH_ACTION, payload, items_key="messages", max_pages=max_pages
        )
        if walk.error_class is not None:
            return PollResult(events=[], cursor=cursor, error_class=walk.error_class)

        events: list[RawEvent] = []
        seen: set[str] = set()
        observed: int | None = None
        for page in walk.pages:
            for message in page:
                event = self._to_event(message)
                if event is None:
                    continue
                message_id = str(event.raw_payload["message_id"])
                if message_id in seen:
                    continue
                seen.add(message_id)
                events.append(event)
                if event.occurred_at is not None:
                    epoch = int(event.occurred_at.timestamp())
                    observed = epoch if observed is None else max(observed, epoch)

        # The hold-or-advance rule lives in _resolve_cursor, never inline: on a
        # truncated walk the remainder was not read, and with nothing observed
        # there is nothing to advance to. `observed` can sit slightly behind the
        # incoming watermark (the window reaches back by OVERLAP_SECONDS); that
        # converges rather than ratchets, because it is a max over the window.
        new_cursor = self._resolve_cursor(
            walk,
            incoming=cursor,
            observed=str(observed) if observed is not None else None,
        )
        logger.info(
            "gmail poll: %d event(s) over %d page(s), cursor %s -> %s",
            len(events),
            len(walk.pages),
            cursor,
            new_cursor,
        )
        return PollResult(events=events, cursor=new_cursor)

    def _to_event(self, message: dict) -> RawEvent | None:
        """Convert one OpenConnector message DTO into a RawEvent."""
        message_id = _first_text(message, "messageId", "id")
        if not message_id:
            logger.warning(
                "gmail message dropped: neither 'messageId' nor 'id' present; keys: %s",
                sorted(message)[:20],
            )
            return None

        headers = _headers_of(message)
        sender = _first_text(message, "sender") or headers.get("From", "") or "unknown"
        subject = _first_text(message, "subject") or headers.get("Subject", "") or "(no subject)"
        labels = message.get("labelIds")

        return RawEvent(
            source="gmail",
            source_account_id="gmail_primary",
            event_type="email_received",
            entity_type="email_thread",
            # Dedup keys off (source, entity_id, message_id), so entity_id must
            # stay the THREAD id — not the message id.
            entity_id=_first_text(message, "threadId") or message_id,
            occurred_at=_occurred_at(message),
            title=subject,
            summary=_snippet_of(message)[:SUMMARY_MAX_CHARS],
            actor={"type": "person", "email": sender, "name": sender},
            raw_payload={
                "message_id": message_id,
                "labels": labels if isinstance(labels, list) else [],
                "to": _first_text(message, "to") or headers.get("To", ""),
                "cc": headers.get("Cc", ""),
                "rfc_message_id": headers.get("Message-ID", ""),
                "in_reply_to": headers.get("In-Reply-To", ""),
                "references": headers.get("References", ""),
                "headers": {
                    name: value
                    for name, value in headers.items()
                    if name.lower() in BULK_MAIL_HEADERS
                },
            },
        )
