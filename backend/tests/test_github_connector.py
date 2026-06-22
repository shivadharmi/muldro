"""Tests for the GitHub connector — Link-header pagination + cursor handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.github_connector import MAX_PAGES, GitHubConnector
from tests.conftest import TEST_USER_ID, make_mock_settings


def _make_notification(notif_id: str, updated_at: str, title: str = "Something") -> dict:
    """Build a minimal GitHub notification object."""
    return {
        "id": notif_id,
        "updated_at": updated_at,
        "reason": "mention",
        "subject": {"type": "Issue", "title": title, "url": "https://api.github.com/x"},
        "repository": {"full_name": "acme/widgets"},
    }


def _resp(status_code: int, payload, link_header: str | None = None) -> MagicMock:
    """Build a mock httpx response with optional RFC5988 Link header."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.headers = {}
    if link_header is not None:
        resp.headers["Link"] = link_header
    return resp


@pytest.mark.asyncio
async def test_github_follows_link_pagination():
    """poll() must follow the Link header rel="next" until exhausted.

    Before the fix the connector read only page 1; any notification on page 2+
    (linked via the GitHub ``Link: <...>; rel="next"`` header) was dropped.
    """
    connector = GitHubConnector(make_mock_settings())

    page1 = _resp(
        200,
        [
            _make_notification("n1", "2026-06-20T09:00:00Z", "First"),
            _make_notification("n2", "2026-06-20T09:30:00Z", "Second"),
        ],
        link_header='<https://api.github.com/notifications?page=2>; rel="next"',
    )
    # Page 2 (final): no rel="next" link.
    page2 = _resp(
        200,
        [_make_notification("n3", "2026-06-20T10:00:00Z", "Third")],
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[page1, page2])
        mock_cls.return_value = mock_client

        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    assert mock_client.get.await_count == 2

    ingested = {e.entity_id for e in result.events}
    assert ingested == {"n1", "n2", "n3"}

    # The second request must target the rel="next" URL.
    second_call = mock_client.get.call_args_list[1]
    second_url = second_call.args[0] if second_call.args else second_call.kwargs.get("url")
    assert second_url == "https://api.github.com/notifications?page=2"


@pytest.mark.asyncio
async def test_github_cursor_is_max_updated_at_not_now():
    """new_cursor must be the MAX updated_at among notifications, not wall-clock now().

    Advancing to now() skips any notification updated between the last item and
    now() forever (a guaranteed event-loss window).
    """
    connector = GitHubConnector(make_mock_settings())

    resp = _resp(
        200,
        [
            _make_notification("n1", "2026-06-20T10:00:00Z", "Earlier"),
            _make_notification("n2", "2026-06-20T11:00:00Z", "Later"),
        ],
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[resp])
        mock_cls.return_value = mock_client

        result = await connector.poll(TEST_USER_ID, "2026-06-20T08:00:00Z", {"access_token": "tok"})

    assert result.ok is True
    # Cursor is the max updated_at, NOT a now()-ish timestamp.
    assert result.cursor == "2026-06-20T11:00:00Z"


@pytest.mark.asyncio
async def test_github_empty_keeps_incoming_cursor():
    """No notifications returned → keep the incoming cursor, never advance to now()."""
    connector = GitHubConnector(make_mock_settings())

    resp = _resp(200, [])

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[resp])
        mock_cls.return_value = mock_client

        result = await connector.poll(TEST_USER_ID, "2026-06-20T08:00:00Z", {"access_token": "tok"})

    assert result.ok is True
    assert result.events == []
    assert result.cursor == "2026-06-20T08:00:00Z"


@pytest.mark.asyncio
async def test_github_rate_limit_preserved_and_cursor_unchanged():
    """Task 2.1's header-aware 403 rate-limit handling must survive pagination refactor.

    A 403 with X-RateLimit-Remaining: 0 is a rate limit, not an auth failure, and
    the cursor must NOT advance on any error.
    """
    connector = GitHubConnector(make_mock_settings())

    resp = MagicMock()
    resp.status_code = 403
    resp.headers = {"X-RateLimit-Remaining": "0"}
    resp.json.return_value = {}

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[resp])
        mock_cls.return_value = mock_client

        result = await connector.poll(TEST_USER_ID, "2026-06-20T08:00:00Z", {"access_token": "tok"})

    assert result.error_class == "rate_limited"
    assert result.cursor == "2026-06-20T08:00:00Z"
    assert result.events == []


@pytest.mark.asyncio
async def test_github_pagination_respects_max_pages_cap():
    """A provider that always returns rel="next" must be bounded by MAX_PAGES."""
    connector = GitHubConnector(make_mock_settings())

    def _always_next(*args, **kwargs):
        return _resp(
            200,
            [_make_notification("n", "2026-06-20T10:00:00Z")],
            link_header='<https://api.github.com/notifications?page=99>; rel="next"',
        )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_always_next)
        mock_cls.return_value = mock_client

        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    assert mock_client.get.await_count == MAX_PAGES
