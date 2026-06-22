"""Tests for the Slack connector — channel + history pagination and per-channel cursors.

Task 3.4 fixes three defects:
1. A single global ``ts`` cursor was applied as ``oldest`` to EVERY channel, so a
   chatty channel's high watermark skipped quiet channels' older messages.
2. ``conversations.list`` was capped at ~10 channels with no pagination.
3. ``conversations.history`` was capped at ``limit:10`` with ``next_cursor`` never
   followed, so active channels dropped messages.

The cursor is now a JSON map ``{channel_id: last_ts}`` serialized into the opaque
``PollResult.cursor`` string the perception layer persists verbatim.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.slack_connector import MAX_PAGES, SlackConnector
from tests.conftest import TEST_USER_ID, make_mock_settings


def _resp(status_code: int, payload: dict) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def _msg(ts: str, text: str = "hi", user: str = "U1") -> dict:
    """Minimal Slack message object."""
    return {"ts": ts, "text": text, "user": user}


def _channels_page(channel_ids: list[str], next_cursor: str | None = None) -> dict:
    """Build a conversations.list page payload."""
    payload: dict = {
        "ok": True,
        "channels": [{"id": cid, "name": cid.lower()} for cid in channel_ids],
    }
    if next_cursor:
        payload["response_metadata"] = {"next_cursor": next_cursor}
    return payload


def _history_page(messages: list[dict], next_cursor: str | None = None) -> dict:
    """Build a conversations.history page payload."""
    payload: dict = {"ok": True, "messages": messages}
    if next_cursor:
        payload["response_metadata"] = {"next_cursor": next_cursor}
    return payload


def _install_mock(mock_cls, dispatch):
    """Wire an httpx.AsyncClient mock whose .get dispatches via a callable.

    ``dispatch(url, params)`` must return a mock response. Both
    conversations.list and conversations.history share the same client.get,
    so dispatch routes by URL.
    """
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def _get(url, params=None, headers=None, timeout=None):
        return dispatch(url, params or {})

    mock_client.get = AsyncMock(side_effect=_get)
    mock_cls.return_value = mock_client
    return mock_client


@pytest.mark.asyncio
async def test_slack_history_paginates():
    """conversations.history must follow response_metadata.next_cursor to completion."""
    connector = SlackConnector(make_mock_settings())

    channels = _channels_page(["C1"])
    hist_page1 = _history_page([_msg(f"100.{i:03d}") for i in range(10)], next_cursor="HCURSOR")
    hist_page2 = _history_page([_msg("101.000")])  # final page, no next_cursor

    history_pages = iter([hist_page1, hist_page2])

    def dispatch(url, params):
        if "conversations.list" in url:
            return _resp(200, channels)
        if "conversations.history" in url:
            return _resp(200, next(history_pages))
        raise AssertionError(f"unexpected url {url}")

    with patch("httpx.AsyncClient") as mock_cls:
        _install_mock(mock_cls, dispatch)
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    # All 11 messages across both pages ingested.
    assert len(result.events) == 11
    # Watermark advanced to the channel's max ts.
    cursor_map = json.loads(result.cursor)
    assert cursor_map["C1"] == "101.000"


@pytest.mark.asyncio
async def test_slack_channel_list_paginates():
    """conversations.list must follow response_metadata.next_cursor → ALL channels polled."""
    connector = SlackConnector(make_mock_settings())

    list_page1 = _channels_page(["C1", "C2"], next_cursor="LCURSOR")
    list_page2 = _channels_page(["C3"])  # final page
    list_pages = iter([list_page1, list_page2])

    polled_channels: set[str] = set()

    def dispatch(url, params):
        if "conversations.list" in url:
            return _resp(200, next(list_pages))
        if "conversations.history" in url:
            polled_channels.add(params["channel"])
            return _resp(200, _history_page([_msg("100.000")]))
        raise AssertionError(f"unexpected url {url}")

    with patch("httpx.AsyncClient") as mock_cls:
        _install_mock(mock_cls, dispatch)
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    # Every channel across both list pages was polled (not capped at 10/20).
    assert polled_channels == {"C1", "C2", "C3"}


@pytest.mark.asyncio
async def test_slack_per_channel_cursor_isolation():
    """A chatty channel's high watermark must not skip a quiet channel's older messages.

    Persisted cursor is a per-channel map. Each channel polls with its OWN oldest
    and advances to its OWN max ts.
    """
    connector = SlackConnector(make_mock_settings())

    # Incoming cursor: chatty channel C_CHATTY at a high watermark, quiet channel
    # C_QUIET at a low watermark.
    incoming = json.dumps({"C_CHATTY": "900.000", "C_QUIET": "100.000"})

    channels = _channels_page(["C_CHATTY", "C_QUIET"])
    oldest_seen: dict[str, str] = {}

    def dispatch(url, params):
        if "conversations.list" in url:
            return _resp(200, channels)
        if "conversations.history" in url:
            ch = params["channel"]
            oldest_seen[ch] = params.get("oldest")
            if ch == "C_CHATTY":
                return _resp(200, _history_page([_msg("905.000")]))
            # Quiet channel returns an older message (101) — must NOT be skipped by
            # the chatty channel's 900 watermark.
            return _resp(200, _history_page([_msg("101.000")]))
        raise AssertionError(f"unexpected url {url}")

    with patch("httpx.AsyncClient") as mock_cls:
        _install_mock(mock_cls, dispatch)
        result = await connector.poll(TEST_USER_ID, incoming, {"access_token": "tok"})

    assert result.ok is True
    # Each channel was queried with its OWN oldest, not a shared global watermark.
    assert oldest_seen["C_CHATTY"] == "900.000"
    assert oldest_seen["C_QUIET"] == "100.000"

    # The quiet channel's older message was ingested (not skipped).
    ingested_ts = {e.raw_payload["ts"] for e in result.events}
    assert "101.000" in ingested_ts
    assert "905.000" in ingested_ts

    # Persisted cursor is a per-channel map; each watermark advanced to its own max.
    cursor_map = json.loads(result.cursor)
    assert cursor_map["C_CHATTY"] == "905.000"
    assert cursor_map["C_QUIET"] == "101.000"


@pytest.mark.asyncio
async def test_slack_legacy_bare_string_cursor_tolerated():
    """A legacy non-JSON cursor must not crash — treat as empty map (fresh per channel)."""
    connector = SlackConnector(make_mock_settings())

    channels = _channels_page(["C1"])

    def dispatch(url, params):
        if "conversations.list" in url:
            return _resp(200, channels)
        if "conversations.history" in url:
            # No oldest applied since legacy cursor is discarded.
            assert "oldest" not in params
            return _resp(200, _history_page([_msg("100.000")]))
        raise AssertionError(f"unexpected url {url}")

    with patch("httpx.AsyncClient") as mock_cls:
        _install_mock(mock_cls, dispatch)
        result = await connector.poll(TEST_USER_ID, "1700000000.000100", {"access_token": "tok"})

    assert result.ok is True
    cursor_map = json.loads(result.cursor)
    assert cursor_map["C1"] == "100.000"


@pytest.mark.asyncio
async def test_slack_channel_error_returns_empty_and_unchanged_cursor():
    """A per-channel failure aborts the whole poll: empty events + INCOMING cursor.

    The connector follows the established "fail -> empty + unchanged cursor"
    contract shared by all connectors. The pipeline never ingests events nor
    advances the cursor on a failing poll (cursor advances only on error_class
    none), so returning partial events / a partially-advanced channel map would
    be silently discarded by the consumer. Instead the failure is surfaced via
    error_class (the breaker sees it) while events stay empty and the cursor map
    is returned UNCHANGED (== incoming), so nothing advances.
    """
    connector = SlackConnector(make_mock_settings())

    incoming = json.dumps({"C_OK": "100.000", "C_FAIL": "200.000"})
    channels = _channels_page(["C_OK", "C_FAIL"])

    def dispatch(url, params):
        if "conversations.list" in url:
            return _resp(200, channels)
        if "conversations.history" in url:
            ch = params["channel"]
            if ch == "C_OK":
                return _resp(200, _history_page([_msg("150.000")]))
            # C_FAIL rate-limited (HTTP 429).
            return _resp(429, {})
        raise AssertionError(f"unexpected url {url}")

    with patch("httpx.AsyncClient") as mock_cls:
        _install_mock(mock_cls, dispatch)
        result = await connector.poll(TEST_USER_ID, incoming, {"access_token": "tok"})

    # Failure visible to the circuit breaker.
    assert result.error_class == "rate_limited"
    assert result.failed is True

    # NO partial events survive a failing poll — the consumer would drop them anyway.
    assert result.events == []

    # Cursor returned UNCHANGED (== incoming) so NOTHING advances, including the
    # channel that happened to drain. Honest about the pipeline invariant.
    assert result.cursor == incoming
    cursor_map = json.loads(result.cursor)
    assert cursor_map["C_OK"] == "100.000"
    assert cursor_map["C_FAIL"] == "200.000"


@pytest.mark.asyncio
async def test_slack_channel_list_error_aborts_without_advancing():
    """A conversations.list failure aborts the poll with the incoming cursor unchanged."""
    connector = SlackConnector(make_mock_settings())

    incoming = json.dumps({"C1": "100.000"})

    def dispatch(url, params):
        if "conversations.list" in url:
            return _resp(429, {})
        raise AssertionError("history must not be called when list fails")

    with patch("httpx.AsyncClient") as mock_cls:
        _install_mock(mock_cls, dispatch)
        result = await connector.poll(TEST_USER_ID, incoming, {"access_token": "tok"})

    assert result.error_class == "rate_limited"
    assert result.cursor == incoming
    assert result.events == []


@pytest.mark.asyncio
async def test_slack_history_pagination_respects_max_pages_cap():
    """A channel whose history always returns next_cursor must be bounded by MAX_PAGES."""
    connector = SlackConnector(make_mock_settings())

    channels = _channels_page(["C1"])
    history_calls = 0

    def dispatch(url, params):
        nonlocal history_calls
        if "conversations.list" in url:
            return _resp(200, channels)
        if "conversations.history" in url:
            history_calls += 1
            return _resp(200, _history_page([_msg("100.000")], next_cursor="ALWAYS"))
        raise AssertionError(f"unexpected url {url}")

    with patch("httpx.AsyncClient") as mock_cls:
        _install_mock(mock_cls, dispatch)
        result = await connector.poll(TEST_USER_ID, None, {"access_token": "tok"})

    assert result.ok is True
    # One channel, capped at MAX_PAGES history requests.
    assert history_calls == MAX_PAGES


@pytest.mark.asyncio
async def test_slack_no_access_token_auth_failed():
    """Missing access token returns auth_failed with the incoming cursor."""
    connector = SlackConnector(make_mock_settings())
    result = await connector.poll(TEST_USER_ID, "x", {})
    assert result.error_class == "auth_failed"
    assert result.cursor == "x"
