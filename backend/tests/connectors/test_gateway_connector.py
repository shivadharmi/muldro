"""GatewayConnector base: error mapping, pagination, cursor sanity."""

from datetime import datetime, timedelta, timezone

import pytest

from src.connectors.gateway_connector import CURSOR_FLOOR_DAYS, GatewayConnector
from src.connectors.poll_result import PollResult


class _FakeCaller:
    """Substitutes GatewayToolCaller; records calls and replays queued results."""

    def __init__(self, results: list[dict]):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call(self, action_id: str, payload: dict) -> dict:
        self.calls.append((action_id, dict(payload)))
        return self._results.pop(0) if self._results else {"status": "ok", "result": {}}


class _Probe(GatewayConnector):
    """Minimal concrete subclass so the base can be exercised directly."""

    provider = "probe"
    cursor_type = "timestamp"
    READ_ACTION = "gmail.get_profile"

    async def poll(self, user_id, cursor, credentials):  # pragma: no cover - unused
        return PollResult()

    async def get_auth_url(self, scopes=None):  # pragma: no cover - unused
        return ""


def _probe(results: list[dict]) -> tuple[_Probe, _FakeCaller]:
    caller = _FakeCaller(results)
    return _Probe(settings=None, caller=caller), caller


async def test_call_returns_ok_payload():
    conn, _ = _probe([{"status": "ok", "result": {"messages": [1, 2]}}])
    ok, data, err = await conn._call("gmail.fetch_emails", {"query": "x"})
    assert ok is True
    assert data == {"messages": [1, 2]}
    assert err is None


async def test_call_maps_error_code_to_poll_error_class():
    conn, _ = _probe([{"status": "error", "error": "boom", "error_code": "rate_limit"}])
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert ok is False
    assert err == "rate_limited"


async def test_call_maps_auth_required_to_transient():
    conn, _ = _probe([{"status": "error", "error": "no cred", "error_code": "auth_required"}])
    _, _, err = await conn._call("gmail.fetch_emails", {})
    assert err == "transient"


async def test_call_maps_unknown_code_to_transient():
    conn, _ = _probe([{"status": "error", "error": "?", "error_code": "surprise"}])
    _, _, err = await conn._call("gmail.fetch_emails", {})
    assert err == "transient"


async def test_walk_pages_follows_page_token_and_stops():
    conn, caller = _probe(
        [
            {"status": "ok", "result": {"messages": [{"id": "a"}], "nextPageToken": "p2"}},
            {"status": "ok", "result": {"messages": [{"id": "b"}]}},
        ]
    )
    pages, err = await conn._walk_pages(
        "gmail.fetch_emails", {"query": "x"}, items_key="messages", max_pages=5
    )
    assert err is None
    assert [p["id"] for page in pages for p in page] == ["a", "b"]
    assert caller.calls[0][1].get("pageToken") is None
    assert caller.calls[1][1]["pageToken"] == "p2"


async def test_walk_pages_stops_at_cap_and_reports_truncation(caplog):
    conn, _ = _probe(
        [
            {"status": "ok", "result": {"messages": [{"id": "a"}], "nextPageToken": "p2"}},
            {"status": "ok", "result": {"messages": [{"id": "b"}], "nextPageToken": "p3"}},
        ]
    )
    import logging

    with caplog.at_level(logging.WARNING):
        pages, err = await conn._walk_pages(
            "gmail.fetch_emails", {"query": "x"}, items_key="messages", max_pages=2
        )
    assert err is None
    assert len(pages) == 2
    assert any("truncat" in r.getMessage().lower() for r in caplog.records)


async def test_walk_pages_propagates_a_failure_without_partial_success():
    conn, _ = _probe(
        [
            {"status": "ok", "result": {"messages": [{"id": "a"}], "nextPageToken": "p2"}},
            {"status": "error", "error": "down", "error_code": "server_error"},
        ]
    )
    pages, err = await conn._walk_pages(
        "gmail.fetch_emails", {"query": "x"}, items_key="messages", max_pages=5
    )
    assert err == "transient"
    assert pages == [], "a failed walk must not hand back partial pages"


def test_epoch_cursor_inside_the_window_is_accepted():
    conn, _ = _probe([])
    now = datetime.now(timezone.utc)
    recent = str(int((now - timedelta(days=1)).timestamp()))
    assert conn._sane_epoch_cursor(recent) is not None


def test_stale_history_id_shaped_cursor_is_rejected():
    """The 1970 trap: a Gmail historyId parses as a valid epoch.

    int("1234567") is 1970-01-15, so `after:1234567` would sweep the entire
    mailbox. The guard must reject on plausibility, not on parseability.
    """
    conn, _ = _probe([])
    assert conn._sane_epoch_cursor("1234567") is None


def test_future_cursor_is_rejected():
    conn, _ = _probe([])
    far = str(int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()))
    assert conn._sane_epoch_cursor(far) is None


def test_cursor_older_than_the_floor_is_rejected():
    conn, _ = _probe([])
    old = datetime.now(timezone.utc) - timedelta(days=CURSOR_FLOOR_DAYS + 5)
    assert conn._sane_epoch_cursor(str(int(old.timestamp()))) is None


def test_non_numeric_cursor_is_rejected():
    conn, _ = _probe([])
    assert conn._sane_epoch_cursor("AbC-syncToken-xyz") is None


def test_none_cursor_is_rejected_without_raising():
    conn, _ = _probe([])
    assert conn._sane_epoch_cursor(None) is None


def test_rejected_cursor_is_logged_with_its_value(caplog):
    """A real bug must not hide behind the self-healing fallback."""
    import logging

    conn, _ = _probe([])
    with caplog.at_level(logging.WARNING):
        conn._sane_epoch_cursor("1234567")
    assert any("1234567" in r.getMessage() for r in caplog.records)


def test_rfc3339_cursor_round_trip():
    conn, _ = _probe([])
    now = datetime.now(timezone.utc) - timedelta(hours=2)
    stamp = now.isoformat().replace("+00:00", "Z")
    assert conn._sane_rfc3339_cursor(stamp) is not None
    assert conn._sane_rfc3339_cursor("not-a-timestamp") is None
    assert conn._sane_rfc3339_cursor(None) is None


async def test_get_auth_url_is_not_offered_by_the_base():
    """Native OAuth for gateway providers was deleted; the base must point at connect."""

    class _NoAuth(GatewayConnector):
        provider = "probe"
        READ_ACTION = "gmail.get_profile"

        async def poll(self, user_id, cursor, credentials):
            return PollResult()

    conn = _NoAuth(settings=None, caller=_FakeCaller([]))
    with pytest.raises(NotImplementedError) as exc:
        await conn.get_auth_url()
    assert "/v1/connections/begin" in str(exc.value)


async def test_test_uses_the_read_action_and_reports_healthy():
    conn, caller = _probe([{"status": "ok", "result": {"emailAddress": "a@b.c"}}])
    health = await conn.test({})
    assert health.status == "healthy"
    assert caller.calls[0][0] == "gmail.get_profile"


async def test_test_reports_down_on_error():
    conn, _ = _probe([{"status": "error", "error": "nope", "error_code": "auth_error"}])
    health = await conn.test({})
    assert health.status == "down"
