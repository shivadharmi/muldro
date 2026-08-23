"""Notion connector — gateway search payload, watermark policy, normalization.

The connector no longer speaks the Notion REST API, so the old httpx status-code
tests are gone with that transport: an HTTP 401 never reaches this layer now,
it arrives as an OpenConnector error code that ``GatewayConnector`` classifies.

The behaviour that IS specific to Notion and worth pinning here is the watermark
strategy. ``notion.search`` offers no ``last_edited_time`` range, so the window
cannot be narrowed server-side. The connector therefore sorts NEWEST-FIRST and
stops at the first page containing an already-seen edit — an inversion of the
native connector, which sorted oldest-first and skipped client-side. That old
shape re-walked the workspace from page one every poll and, past ``MAX_PAGES``,
truncated before reaching anything new; ``_resolve_cursor`` then held the cursor
and the next poll read the very same pages, for ever.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.connectors.notion_connector import MAX_PAGES, PAGE_SIZE, NotionConnector
from tests.conftest import TEST_USER_ID, make_mock_settings

SEARCH_ACTION = "notion.search"


@dataclass(frozen=True)
class _Raw:
    envelope: dict


def _envelope(payload: dict) -> dict:
    """The four-hop transport envelope, as tests/test_calendar_connector.py derives it."""
    return {"status": "ok", "result": json.dumps({"ok": True, "data": payload})}


class _FakeCaller:
    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call(self, action_id: str, payload: dict) -> dict:
        self.calls.append((action_id, dict(payload)))
        item = self._results.pop(0) if self._results else {}
        if isinstance(item, _Raw):
            return item.envelope
        return _envelope(item)


def _connector(results: list) -> tuple[NotionConnector, _FakeCaller]:
    caller = _FakeCaller(results)
    return NotionConnector(make_mock_settings(), caller=caller), caller


def _stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _page(page_id: str, edited: str, *, created: str | None = None, title: str = "Spec") -> dict:
    """A Notion page row as ``notion.search`` returns it."""
    return {
        "object": "page",
        "id": page_id,
        "created_time": created or "2026-01-01T00:00:00.000Z",
        "last_edited_time": edited,
        "url": f"https://notion.so/{page_id}",
        "last_edited_by": {"object": "user", "type": "person", "name": "Alice"},
        "properties": {"title": {"title": [{"plain_text": title}]}},
    }


def _results(rows: list[dict], next_cursor: str | None = None) -> dict:
    return {
        "object": "list",
        "results": rows,
        "has_more": next_cursor is not None,
        "next_cursor": next_cursor,
    }


# ---- the search payload --------------------------------------------------


async def test_search_is_sent_newest_first_with_an_empty_query():
    """The empty query is what turns a search action into an enumeration.

    ``notion.search`` marks ``query`` required but declares NO minLength, so ""
    is legal and Notion reads it as "everything". Descending order is what makes
    the watermark stop sound.
    """
    connector, caller = _connector([_results([])])
    await connector.poll(TEST_USER_ID, None, {})

    action_id, payload = caller.calls[0]
    assert action_id == SEARCH_ACTION
    assert payload["query"] == ""
    assert payload["sort"] == {"direction": "descending", "timestamp": "last_edited_time"}
    assert payload["filter"] == {"property": "object", "value": "page"}
    assert payload["pageSize"] == PAGE_SIZE


async def test_poll_emits_events_and_advances_to_the_newest_edit():
    newest = "2026-06-21T12:00:00.000Z"
    older = "2026-06-21T09:00:00.000Z"
    connector, _ = _connector([_results([_page("p1", newest), _page("p2", older)])])

    result = await connector.poll(TEST_USER_ID, None, {})

    assert [e.entity_id for e in result.events] == ["p1", "p2"]
    assert result.cursor == newest
    assert result.error_class == "none"


async def test_an_empty_workspace_holds_the_incoming_cursor():
    """Nothing observed means nothing to advance to — never jump to now()."""
    cursor = _stamp(datetime.now(timezone.utc) - timedelta(days=1))
    connector, _ = _connector([_results([])])

    result = await connector.poll(TEST_USER_ID, cursor, {})

    assert result.events == []
    assert result.cursor == cursor


# ---- the watermark stop --------------------------------------------------


async def test_the_walk_stops_at_the_first_already_seen_edit():
    """Descending order makes one known row prove the whole tail is known.

    The second page must never be fetched: that is the entire cost saving over
    the oldest-first walk this replaced.
    """
    now = datetime.now(timezone.utc)
    cursor = _stamp(now - timedelta(hours=2))
    fresh = _stamp(now - timedelta(minutes=5))
    stale = _stamp(now - timedelta(hours=6))

    connector, caller = _connector(
        [
            _results([_page("new", fresh), _page("old", stale)], next_cursor="c2"),
            _results([_page("older", _stamp(now - timedelta(days=3)))]),
        ]
    )
    result = await connector.poll(TEST_USER_ID, cursor, {})

    assert len(caller.calls) == 1, "the walk must stop rather than page on"
    assert [e.entity_id for e in result.events] == ["new"]
    assert result.cursor == fresh


async def test_rows_at_or_below_the_watermark_are_not_re_emitted():
    """The boundary is exclusive: an edit exactly at the cursor is already known."""
    now = datetime.now(timezone.utc)
    cursor = _stamp(now - timedelta(hours=1))
    connector, _ = _connector([_results([_page("boundary", cursor)])])

    result = await connector.poll(TEST_USER_ID, cursor, {})

    assert result.events == []
    assert result.cursor == cursor


async def test_a_truncated_walk_of_new_rows_holds_the_cursor():
    """More new edits than one poll drains is an UNDRAINED window.

    Advancing to the newest row would skip everything below it that was never
    read. Holding re-reads the window next poll; dedup absorbs the overlap.
    """
    now = datetime.now(timezone.utc)
    cursor = _stamp(now - timedelta(days=10))
    pages = [
        _results([_page(f"p{i}", _stamp(now - timedelta(minutes=i)))], next_cursor=f"c{i}")
        for i in range(MAX_PAGES)
    ]
    connector, caller = _connector(pages)

    result = await connector.poll(TEST_USER_ID, cursor, {})

    assert len(caller.calls) == MAX_PAGES
    assert result.events, "the rows it did read are still emitted"
    assert result.cursor == cursor, "an undrained window must not advance"


async def test_pagination_follows_next_cursor_under_notion_key_names():
    """Notion pages on startCursor/next_cursor, not pageToken/nextPageToken."""
    now = datetime.now(timezone.utc)
    connector, caller = _connector(
        [
            _results([_page("p1", _stamp(now - timedelta(minutes=1)))], next_cursor="CURSOR2"),
            _results([_page("p2", _stamp(now - timedelta(minutes=2)))]),
        ]
    )
    await connector.poll(TEST_USER_ID, None, {})

    assert len(caller.calls) == 2
    assert caller.calls[1][1]["startCursor"] == "CURSOR2"


# ---- guards --------------------------------------------------------------


async def test_a_row_with_no_id_is_skipped():
    """An empty entity_id collapses the idempotency key — silent event loss."""
    edited = _stamp(datetime.now(timezone.utc))
    row = _page("", edited)
    connector, _ = _connector([_results([row])])

    result = await connector.poll(TEST_USER_ID, None, {})

    assert result.events == []


async def test_a_row_with_no_timestamp_is_skipped():
    """Unstamped rows can be neither watermarked nor ordered."""
    row = _page("p1", "")
    del row["last_edited_time"]
    connector, _ = _connector([_results([row])])

    result = await connector.poll(TEST_USER_ID, None, {})

    assert result.events == []
    assert result.cursor is None


async def test_a_failed_call_never_advances_the_cursor():
    cursor = _stamp(datetime.now(timezone.utc) - timedelta(hours=3))
    failure = _Raw({"status": "ok", "result": json.dumps({"ok": False, "error": {"code": "x"}})})
    connector, _ = _connector([failure])

    result = await connector.poll(TEST_USER_ID, cursor, {})

    assert result.events == []
    assert result.cursor == cursor
    assert result.error_class != "none"


async def test_a_missing_results_key_is_a_failure_not_an_empty_page():
    """Absent `results` means we are looking at the wrong object, not at no pages."""
    cursor = _stamp(datetime.now(timezone.utc) - timedelta(hours=3))
    connector, _ = _connector([{"object": "list", "has_more": False}])

    result = await connector.poll(TEST_USER_ID, cursor, {})

    assert result.error_class == "transient"
    assert result.cursor == cursor


# ---- normalization -------------------------------------------------------


async def test_created_and_updated_are_distinguished():
    same = "2026-06-21T12:00:00.000Z"
    later = "2026-06-22T12:00:00.000Z"
    connector, _ = _connector(
        [
            _results(
                [
                    _page("created", same, created=same),
                    _page("updated", later, created=same),
                ]
            )
        ]
    )

    result = await connector.poll(TEST_USER_ID, None, {})
    by_id = {e.entity_id: e for e in result.events}

    assert by_id["created"].event_type == "page_created"
    assert by_id["updated"].event_type == "page_updated"
    assert by_id["created"].source == "notion"
    assert by_id["created"].entity_type == "page"
    assert by_id["created"].title == "Spec"
