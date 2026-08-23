"""Notion connector — polls page edits through the OpenConnector gateway.

This connector no longer speaks the Notion REST API. It calls ``notion.search``
through the gateway and inherits envelope handling, pagination and cursor policy
from :class:`GatewayConnector`. Retiring its native OAuth is what removes the
last stdio secret on this provider: the ``@notionhq/notion-mcp-server`` process
it replaced was launched with ``NOTION_TOKEN`` resolved out of the environment,
leaving the token readable in ``ps aux`` for as long as the child lived.

**The empty query is load-bearing.** ``notion.search`` marks ``query`` required,
but the live OpenConnector v1.3.5 schema declares it with NO ``minLength`` — so
``""`` is legal, and Notion reads an empty query as "everything". This is the
one thing that lets the whole workspace be enumerated through an action built
for search. (``slack.search_messages`` sets ``minLength: 1`` and therefore
cannot be driven this way; it needs a real query carrying its own window.)

**Newest-first, not oldest-first — and that is a fix, not a transcription.**
The native connector sorted ASCENDING and skipped rows at or below the cursor
client-side, because ``/v1/search`` accepts no ``last_edited_time`` range and
the watermark had to be enforced locally either way. But oldest-first means
every poll re-walks the workspace from its very first page, and once the page
count passes ``MAX_PAGES`` the walk truncates *before* reaching anything new.
``_resolve_cursor`` then correctly holds the cursor — and the next poll reads
exactly the same pages. A workspace large enough to truncate would observe
nothing, for ever, while reporting healthy polls.

Sorting descending inverts that: new edits are on page one, and the walk stops
at the first row already at or below the watermark. Cost becomes proportional to
what changed rather than to workspace size, and truncation now means "there are
more NEW rows than one poll drains" — a real undrained window, which
``_resolve_cursor`` holds for, so the remainder is picked up next poll instead
of skipped.

**Deletions do not arrive**, and did not before either: Notion's search omits
trashed pages rather than tombstoning them, so an archived page is observed only
as the edit that archived it, if that edit lands inside a polled window.
"""

import logging
from datetime import datetime, timezone

from src.connectors.base import register_connector
from src.connectors.gateway_connector import GatewayConnector
from src.connectors.poll_result import PollResult
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

# Defensive page cap for a single poll. Newest-first means a cap is reached only
# when more pages of NEW edits exist than one poll drains, so truncation holds
# the cursor and the remainder arrives next poll — it is a throttle, not a loss.
MAX_PAGES = 10

# Pages requested per call. The action's schema allows up to 100.
PAGE_SIZE = 50

# Notion sorts by ``last_edited_time``; ``object: "page"`` drops data sources and
# databases, which carry no reader-facing edit signal.
_SORT_NEWEST_FIRST: dict = {"direction": "descending", "timestamp": "last_edited_time"}
_FILTER_PAGES_ONLY: dict = {"property": "object", "value": "page"}


def _parse_edited(row: dict) -> datetime | None:
    """Parse a row's ``last_edited_time``, or None if it has no usable stamp.

    Returns an aware datetime so every comparison in this module is between two
    parsed instants. Notion documents this field as RFC 3339 with a "Z" suffix,
    but the parse is guarded anyway: a malformed stamp must skip one row, not
    raise out of the poll and fail the whole window.
    """
    raw = row.get("last_edited_time")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


@register_connector("notion")
class NotionConnector(GatewayConnector):
    """Polls Notion page edits through the gateway, watermarked on last_edited_time."""

    cursor_type: str = "timestamp"

    # Cheapest read for the health probe: it takes no ids and no query.
    READ_ACTION = "notion.list_users"
    SEARCH_ACTION = "notion.search"

    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Poll Notion for pages edited since the cursor watermark.

        ``credentials`` is unused — the credential lives in OpenConnector and the
        gateway binds it per connection. The parameter stays because
        ``BaseConnector.poll`` defines it.
        """
        watermark = self._sane_rfc3339_cursor(cursor)

        def _reached_watermark(rows: list[dict]) -> bool:
            """True once a page contains an edit we have already seen.

            Sound only because the sort is descending: the first row at or below
            the watermark means every later row is too. On an initial sync
            (no watermark) nothing is already known, so the walk runs to its cap.
            """
            if watermark is None:
                return False
            return any(
                (parsed := _parse_edited(row)) is not None and parsed <= watermark for row in rows
            )

        payload: dict = {
            "query": "",
            "sort": _SORT_NEWEST_FIRST,
            "filter": _FILTER_PAGES_ONLY,
            "pageSize": PAGE_SIZE,
        }

        walk = await self._walk_pages(
            self.SEARCH_ACTION,
            payload,
            items_key="results",
            max_pages=MAX_PAGES,
            page_token_key="startCursor",
            next_token_key="next_cursor",
            stop_when=_reached_watermark,
        )
        if walk.error_class is not None:
            return PollResult(events=[], cursor=cursor, error_class=walk.error_class)

        events: list[RawEvent] = []
        # The cursor is carried as the row's OWN raw string so it round-trips in
        # Notion's format, while the ordering is decided on the parsed value --
        # comparing the strings would require both sides to be byte-identical in
        # precision, which a re-rendered cursor is not (".105Z" vs ".105000Z"
        # compares as NEWER, so the boundary row is re-emitted every poll).
        observed_raw: str | None = None
        observed_at: datetime | None = None
        for page in walk.pages:
            for item in page:
                parsed = _parse_edited(item)
                if parsed is None:
                    # Without a usable stamp the row can be neither watermarked
                    # nor ordered, and emitting it would collide the idempotency
                    # key with every other stampless row.
                    logger.warning("Notion row %r has no usable last_edited_time", item.get("id"))
                    continue
                if not item.get("id"):
                    # An empty entity_id collapses the idempotency key, so two
                    # id-less pages edited in the same instant would dedup into
                    # one — silent event loss rather than a visible failure.
                    logger.warning("Notion row has no id; skipping to avoid a key collision")
                    continue
                # The stop predicate ends the walk at the first page CONTAINING a
                # known row, so that page still carries known rows after it.
                if watermark is not None and parsed <= watermark:
                    continue
                event = self._normalize_page(item)
                if event:
                    events.append(event)
                if observed_at is None or parsed > observed_at:
                    observed_at = parsed
                    observed_raw = item["last_edited_time"]

        new_cursor = self._resolve_cursor(walk, incoming=cursor, observed=observed_raw)
        logger.info("Notion poll: %d events, cursor %s -> %s", len(events), cursor, new_cursor)
        return PollResult(events=events, cursor=new_cursor)

    @staticmethod
    def _normalize_page(page: dict) -> RawEvent | None:
        title_prop = page.get("properties", {}).get("title", {})
        title_parts = title_prop.get("title", []) if title_prop else []
        title = title_parts[0].get("plain_text", "") if title_parts else "(untitled)"

        edited = page.get("last_edited_time", "")
        created = page.get("created_time", "")
        event_type = "page_created" if edited == created else "page_updated"

        occurred_at = None
        if edited:
            try:
                occurred_at = datetime.fromisoformat(edited.replace("Z", "+00:00"))
            except ValueError:
                pass

        edited_by = page.get("last_edited_by", {})

        return RawEvent(
            source="notion",
            source_account_id="notion_primary",
            event_type=event_type,
            entity_type="page",
            entity_id=page.get("id", ""),
            occurred_at=occurred_at,
            title=title,
            summary=f"Notion page: {title}",
            actor={
                "type": edited_by.get("type", "person"),
                "name": edited_by.get("name", ""),
            },
            raw_payload={
                "page_id": page.get("id"),
                "url": page.get("url"),
                "last_edited_time": edited,
            },
        )
