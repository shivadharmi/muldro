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
