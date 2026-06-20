"""Tests for the Notion connector — PollResult, pagination, ascending sort, per-edit dedup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.notion_connector import MAX_PAGES, NotionConnector
from src.connectors.poll_result import PollResult
from src.services.event_processor import make_idempotency_key
from tests.conftest import TEST_USER_ID, make_mock_settings, make_raw_event


def _make_page(page_id: str, last_edited_time: str, title: str = "Page") -> dict:
    """Build a minimal Notion page search-result object."""
    return {
        "object": "page",
        "id": page_id,
        "last_edited_time": last_edited_time,
        "created_time": "2026-06-01T00:00:00.000Z",
        "url": f"https://notion.so/{page_id}",
        "last_edited_by": {"type": "person", "name": "Jane"},
        "properties": {"title": {"title": [{"plain_text": title}]}},
    }


def _resp(status_code: int, payload: dict) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = ""
    return resp


def _mock_client(side_effect):
    """Build a patched httpx.AsyncClient whose post() yields the given responses."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=side_effect)
    return mock_client


@pytest.mark.asyncio
async def test_notion_returns_pollresult():
    """A successful poll returns a PollResult instance, not a bare tuple."""
    connector = NotionConnector(make_mock_settings())
    resp = _resp(
        200, {"results": [_make_page("p1", "2026-06-20T09:00:00.000Z")], "has_more": False}
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([resp])
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert isinstance(result, PollResult)
    assert result.ok is True
    assert {e.entity_id for e in result.events} == {"p1"}


@pytest.mark.asyncio
async def test_notion_429_is_rate_limited():
    """HTTP 429 → error_class='rate_limited', cursor unchanged."""
    connector = NotionConnector(make_mock_settings())
    resp = _resp(429, {"object": "error", "code": "rate_limited"})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([resp])
        result = await connector.poll(
            TEST_USER_ID, "2026-06-20T08:00:00.000Z", {"access_token": "tok"}
        )

    assert result.error_class == "rate_limited"
    assert result.cursor == "2026-06-20T08:00:00.000Z"
    assert result.events == []


@pytest.mark.asyncio
async def test_notion_401_is_auth_failed():
    """HTTP 401 → error_class='auth_failed', cursor unchanged."""
    connector = NotionConnector(make_mock_settings())
    resp = _resp(401, {"object": "error", "code": "unauthorized"})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([resp])
        result = await connector.poll(
            TEST_USER_ID, "2026-06-20T08:00:00.000Z", {"access_token": "tok"}
        )

    assert result.error_class == "auth_failed"
    assert result.cursor == "2026-06-20T08:00:00.000Z"
    assert result.events == []


@pytest.mark.asyncio
async def test_notion_paginates_has_more():
    """A two-page response (has_more + next_cursor) collects ALL pages' results."""
    connector = NotionConnector(make_mock_settings())
    page1 = _resp(
        200,
        {
            "results": [
                _make_page("p1", "2026-06-20T09:00:00.000Z"),
                _make_page("p2", "2026-06-20T09:30:00.000Z"),
            ],
            "has_more": True,
            "next_cursor": "cursor_2",
        },
    )
    page2 = _resp(
        200,
        {"results": [_make_page("p3", "2026-06-20T10:00:00.000Z")], "has_more": False},
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = _mock_client([page1, page2])
        mock_cls.return_value = mock_client
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    assert mock_client.post.await_count == 2
    assert {e.entity_id for e in result.events} == {"p1", "p2", "p3"}
    # second request must carry the next_cursor as start_cursor
    second_call = mock_client.post.call_args_list[1]
    assert second_call.kwargs["json"].get("start_cursor") == "cursor_2"


@pytest.mark.asyncio
async def test_notion_pagination_respects_max_pages_cap():
    """A provider that always returns has_more must be bounded by MAX_PAGES."""
    connector = NotionConnector(make_mock_settings())

    def _always_more(*args, **kwargs):
        return _resp(
            200,
            {
                "results": [_make_page("p", "2026-06-20T10:00:00.000Z")],
                "has_more": True,
                "next_cursor": "more",
            },
        )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = _mock_client(_always_more)
        mock_cls.return_value = mock_client
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    assert mock_client.post.await_count == MAX_PAGES


@pytest.mark.asyncio
async def test_notion_normalization_created_vs_updated():
    """page_created (created==edited) vs page_updated (edited>created) discrimination."""
    connector = NotionConnector(make_mock_settings())

    created = _make_page("p_new", "2026-06-20T09:00:00.000Z", "Brand new")
    created["created_time"] = "2026-06-20T09:00:00.000Z"  # created == edited

    updated = _make_page("p_old", "2026-06-20T10:00:00.000Z", "Edited")
    updated["created_time"] = "2026-06-01T00:00:00.000Z"  # edited > created

    resp = _resp(200, {"results": [created, updated], "has_more": False})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([resp])
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    by_id = {e.entity_id: e for e in result.events}
    assert by_id["p_new"].event_type == "page_created"
    assert by_id["p_old"].event_type == "page_updated"
    # occurred_at parsed from last_edited_time, Z suffix handled → tz-aware.
    assert by_id["p_new"].source == "notion"
    assert by_id["p_new"].occurred_at is not None
    assert by_id["p_new"].occurred_at.tzinfo is not None
    assert by_id["p_old"].occurred_at.hour == 10


@pytest.mark.asyncio
async def test_notion_empty_workspace_cursor_unchanged():
    """An empty workspace (results=[]) → ok, no events, cursor unchanged."""
    connector = NotionConnector(make_mock_settings())
    resp = _resp(200, {"results": [], "has_more": False})

    incoming = "2026-06-20T08:00:00.000Z"
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([resp])
        result = await connector.poll(TEST_USER_ID, incoming, {"access_token": "tok"})

    assert result.ok is True
    assert result.events == []
    # Nothing newer than the cursor → cursor must not jump forward.
    assert result.cursor == incoming


@pytest.mark.asyncio
async def test_notion_cursor_boundary_is_exclusive():
    """An edit exactly equal to the cursor is skipped; only strictly-newer edits emit."""
    connector = NotionConnector(make_mock_settings())

    cursor = "2026-06-20T09:00:00.000Z"
    at_boundary = _make_page("p_boundary", cursor)  # edited == cursor → skipped
    newer = _make_page("p_newer", "2026-06-20T09:30:00.000Z")  # > cursor → emitted

    resp = _resp(200, {"results": [at_boundary, newer], "has_more": False})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([resp])
        result = await connector.poll(TEST_USER_ID, cursor, {"access_token": "tok"})

    assert result.ok is True
    emitted = {e.entity_id for e in result.events}
    assert emitted == {"p_newer"}
    assert "p_boundary" not in emitted
    # Cursor advances to the newest emitted edit.
    assert result.cursor == "2026-06-20T09:30:00.000Z"


@pytest.mark.asyncio
async def test_notion_malformed_page_id_is_guarded():
    """A page with an empty/missing id must not produce a `notion::` idempotency key."""
    connector = NotionConnector(make_mock_settings())

    bad = _make_page("", "2026-06-20T09:30:00.000Z")  # empty id
    del bad["id"]  # missing entirely
    good = _make_page("p_good", "2026-06-20T09:45:00.000Z")

    resp = _resp(200, {"results": [bad, good], "has_more": False})

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_client([resp])
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    # No event with an empty entity_id should leak through (would yield a
    # collision-prone "notion::..." idempotency key).
    keys = {make_idempotency_key(e) for e in result.events}
    assert all(not k.startswith("notion::") for k in keys)
    assert {e.entity_id for e in result.events} == {"p_good"}


def test_notion_repeat_edits_distinct_events():
    """Same page_id edited twice (distinct last_edited_time) → TWO distinct idempotency keys."""
    edit1 = make_raw_event(
        source="notion",
        entity_id="page_42",
        event_type="page_updated",
        raw_payload={
            "page_id": "page_42",
            "last_edited_time": "2026-06-20T09:00:00.000Z",
        },
    )
    edit2 = make_raw_event(
        source="notion",
        entity_id="page_42",
        event_type="page_updated",
        raw_payload={
            "page_id": "page_42",
            "last_edited_time": "2026-06-20T10:00:00.000Z",
        },
    )

    key1 = make_idempotency_key(edit1)
    key2 = make_idempotency_key(edit2)

    assert key1 != key2
    assert key1 == "notion:page_42:2026-06-20T09:00:00.000Z:page_updated"
    assert key2 == "notion:page_42:2026-06-20T10:00:00.000Z:page_updated"
