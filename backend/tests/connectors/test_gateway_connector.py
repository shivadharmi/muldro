"""GatewayConnector base: envelope unwrapping, error mapping, pagination, cursor sanity."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from src.connectors.gateway_connector import CURSOR_FLOOR_DAYS, GatewayConnector
from src.connectors.poll_result import PollResult


@dataclass(frozen=True)
class _Raw:
    """A pre-built MCP envelope, handed back verbatim."""

    envelope: dict


def _raw(envelope: dict) -> _Raw:
    """Queue an envelope the fake must NOT wrap (errors, malformed payloads)."""
    return _Raw(envelope)


def _envelope(payload: dict) -> dict:
    """Build the envelope the REAL transport delivers for ``payload``.

    Derived from the four hops, not invented:
      1. OpenConnector answers ``{"ok": true, "data": <payload>}``
         (infra/gateway/spike-findings-guide.md).
      2. src/adapter/server.py returns that dict through ``_result_to_dict``.
      3. FastMCP serializes the ``-> dict`` tool return into a text block.
      4. session_pool.call_tool joins text blocks -> ``{"status":"ok","result": <str>}``.

    So ``result`` is a JSON **string** wrapping the payload under ``data``. A
    fake that hands back a bare dict asserts a shape that does not exist — this
    project has already shipped one bug (seven nonexistent Gmail action ids)
    whose only cause was tests agreeing with the code instead of with reality.
    """
    return {"status": "ok", "result": json.dumps({"ok": True, "data": payload})}


class _FakeCaller:
    """Substitutes GatewayToolCaller; records calls and replays queued results.

    Queued entries are **provider payloads** (what a connector consumes) and are
    serialized through ``_envelope``. Wrap an entry in ``_raw(...)`` to inject a
    verbatim envelope for the error and malformed-input cases.
    """

    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call(self, action_id: str, payload: dict) -> dict:
        self.calls.append((action_id, dict(payload)))
        item = self._results.pop(0) if self._results else {}
        if isinstance(item, _Raw):
            return item.envelope
        return _envelope(item)


class _Probe(GatewayConnector):
    """Minimal concrete subclass so the base can be exercised directly."""

    provider = "probe"
    cursor_type = "timestamp"
    READ_ACTION = "gmail.get_profile"

    async def poll(self, user_id, cursor, credentials):  # pragma: no cover - unused
        return PollResult()

    async def get_auth_url(self, scopes=None):  # pragma: no cover - unused
        return ""


def _probe(results: list) -> tuple[_Probe, _FakeCaller]:
    caller = _FakeCaller(results)
    return _Probe(settings=None, caller=caller), caller


# ---- _call: the envelope chain ------------------------------------------


async def test_call_parses_the_json_string_result_and_unwraps_data():
    """The real transport hands back a JSON string, not a dict.

    Before this was fixed, `isinstance(result, dict)` was False on 100% of
    successful calls, so every poll returned an empty success and advanced its
    cursor past data it never received.
    """
    conn, _ = _probe([{"messages": [1, 2]}])
    ok, data, err = await conn._call("gmail.fetch_emails", {"query": "x"})
    assert ok is True
    assert data == {"messages": [1, 2]}
    assert err is None


async def test_call_accepts_an_already_structured_dict_result():
    """Robustness if the transport is ever changed to pass structured content."""
    conn, _ = _probe([_raw({"status": "ok", "result": {"ok": True, "data": {"messages": [1]}}})])
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert (ok, data, err) == (True, {"messages": [1]}, None)


async def test_call_treats_unparseable_json_as_failure_not_empty_success():
    conn, _ = _probe([_raw({"status": "ok", "result": "<html>502 Bad Gateway</html>"})])
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert ok is False, "an unreadable payload must never look like an empty window"
    assert data == {}
    assert err == "transient"


async def test_call_truncates_the_unparseable_payload_in_the_log(caplog):
    conn, _ = _probe([_raw({"status": "ok", "result": "x" * 5000})])
    with caplog.at_level(logging.WARNING):
        await conn._call("gmail.fetch_emails", {})
    messages = [r.getMessage() for r in caplog.records]
    assert any("gmail.fetch_emails" in m for m in messages)
    assert all(len(m) < 1000 for m in messages), "must not log an unbounded blob"


async def test_call_treats_a_none_result_as_failure():
    conn, _ = _probe([_raw({"status": "ok", "result": None})])
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert (ok, data, err) == (False, {}, "transient")


async def test_call_treats_a_non_dict_payload_as_failure():
    """A JSON array parses fine but is not a provider payload."""
    conn, _ = _probe([_raw({"status": "ok", "result": "[1, 2]"})])
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert (ok, data, err) == (False, {}, "transient")


async def test_call_treats_openconnector_ok_false_as_failure():
    """An action-level failure arrives inside an MCP transport-level success."""
    conn, _ = _probe(
        [_raw({"status": "ok", "result": json.dumps({"ok": False, "error": "no credential"})})]
    )
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert ok is False, "ok=false is a failed read, not an empty mailbox"
    assert data == {}
    assert err == "transient"


async def test_call_classifies_an_ok_false_error_code():
    conn, _ = _probe(
        [
            _raw(
                {
                    "status": "ok",
                    "result": json.dumps(
                        {"ok": False, "error": "slow down", "error_code": "rate_limit"}
                    ),
                }
            )
        ]
    )
    _, _, err = await conn._call("gmail.fetch_emails", {})
    assert err == "rate_limited"


async def test_call_logs_the_reason_for_an_ok_false_response(caplog):
    conn, _ = _probe(
        [_raw({"status": "ok", "result": json.dumps({"ok": False, "error": "no credential"})})]
    )
    with caplog.at_level(logging.WARNING):
        await conn._call("gmail.fetch_emails", {})
    assert any("no credential" in r.getMessage() for r in caplog.records)


async def test_call_falls_back_to_the_parsed_dict_when_there_is_no_data_key():
    """_result_to_dict has {"content": ...} and {"result": ...} branches too."""
    conn, _ = _probe([_raw({"status": "ok", "result": json.dumps({"content": [{"text": "hi"}]})})])
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert (ok, err) == (True, None)
    assert data == {"content": [{"text": "hi"}]}


async def test_call_treats_a_truthy_error_beside_an_ok_status_as_failure():
    conn, _ = _probe([_raw({"status": "ok", "error": "partial outage", "result": "{}"})])
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert (ok, data) == (False, {})
    assert err == "transient"


async def test_call_without_a_caller_fails_rather_than_reporting_an_empty_window(caplog):
    """The guard against a poller that forgot to inject the transport.

    This whole increment exists because a missing dependency killed perception
    silently; the missing-caller branch must never be a clean empty success.
    """
    conn = _Probe(settings=None, caller=None)
    with caplog.at_level(logging.WARNING):
        ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert (ok, data, err) == (False, {}, "transient")
    assert any("caller" in r.getMessage() for r in caplog.records)


# ---- _call: transport-level failures -------------------------------------


async def test_call_maps_error_code_to_poll_error_class():
    conn, _ = _probe([_raw({"status": "error", "error": "boom", "error_code": "rate_limit"})])
    ok, data, err = await conn._call("gmail.fetch_emails", {})
    assert ok is False
    assert err == "rate_limited"


async def test_call_maps_auth_required_to_transient():
    conn, _ = _probe([_raw({"status": "error", "error": "no cred", "error_code": "auth_required"})])
    _, _, err = await conn._call("gmail.fetch_emails", {})
    assert err == "transient"


async def test_call_maps_unknown_code_to_transient():
    conn, _ = _probe([_raw({"status": "error", "error": "?", "error_code": "surprise"})])
    _, _, err = await conn._call("gmail.fetch_emails", {})
    assert err == "transient"


async def test_call_logs_the_message_key_used_by_make_error_response(caplog):
    """make_error_response and the circuit-open envelope report under "message".

    Reading only "error" logged the literal "failed: None" for timeout,
    rate_limit, server_error, validation_error, not_found and circuit_open —
    every diagnostic vanished exactly when a source started failing.
    """
    conn, _ = _probe(
        [_raw({"status": "error", "error_code": "timeout", "message": "gmail timed out after 30s"})]
    )
    with caplog.at_level(logging.WARNING):
        await conn._call("gmail.fetch_emails", {})
    assert any("gmail timed out after 30s" in r.getMessage() for r in caplog.records)


# ---- _walk_pages ---------------------------------------------------------


async def test_walk_pages_follows_page_token_and_stops():
    conn, caller = _probe(
        [
            {"messages": [{"id": "a"}], "nextPageToken": "p2"},
            {"messages": [{"id": "b"}]},
        ]
    )
    pages, err, truncated = await conn._walk_pages(
        "gmail.fetch_emails", {"query": "x"}, items_key="messages", max_pages=5
    )
    assert err is None
    assert truncated is False
    assert [p["id"] for page in pages for p in page] == ["a", "b"]
    assert caller.calls[0][1].get("pageToken") is None
    assert caller.calls[1][1]["pageToken"] == "p2"


async def test_walk_pages_yields_rows_through_the_real_envelope():
    """End-to-end proof that the JSON-string + `data`-nesting chain is handled.

    Against the pre-fix `_call` this returns a single empty page: the exact
    shape that would make a connector report a clean, empty poll and advance
    its cursor past a full mailbox.
    """
    conn, _ = _probe([{"messages": [{"id": "m1"}, {"id": "m2"}]}])
    pages, err, truncated = await conn._walk_pages(
        "gmail.fetch_emails", {"query": "x"}, items_key="messages", max_pages=5
    )
    assert (err, truncated) == (None, False)
    assert pages == [[{"id": "m1"}, {"id": "m2"}]]


async def test_walk_pages_reports_truncation_structurally(caplog):
    conn, _ = _probe(
        [
            {"messages": [{"id": "a"}], "nextPageToken": "p2"},
            {"messages": [{"id": "b"}], "nextPageToken": "p3"},
        ]
    )
    with caplog.at_level(logging.WARNING):
        pages, err, truncated = await conn._walk_pages(
            "gmail.fetch_emails", {"query": "x"}, items_key="messages", max_pages=2
        )
    assert err is None
    assert truncated is True, "a truncated walk must not look like a complete one"
    assert len(pages) == 2
    warnings = [r for r in caplog.records if "truncat" in r.getMessage().lower()]
    assert len(warnings) == 1, "exactly one truncation warning, not one per page"


async def test_walk_pages_propagates_a_failure_without_partial_success():
    conn, _ = _probe(
        [
            {"messages": [{"id": "a"}], "nextPageToken": "p2"},
            _raw({"status": "error", "error": "down", "error_code": "server_error"}),
        ]
    )
    pages, err, truncated = await conn._walk_pages(
        "gmail.fetch_emails", {"query": "x"}, items_key="messages", max_pages=5
    )
    assert err == "transient"
    assert pages == [], "a failed walk must not hand back partial pages"
    assert truncated is False


# ---- epoch cursor plausibility ------------------------------------------


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
    conn, _ = _probe([])
    with caplog.at_level(logging.WARNING):
        conn._sane_epoch_cursor("1234567")
    assert any("1234567" in r.getMessage() for r in caplog.records)


# ---- RFC 3339 cursor plausibility ---------------------------------------


def test_rfc3339_cursor_round_trip():
    conn, _ = _probe([])
    now = datetime.now(timezone.utc) - timedelta(hours=2)
    stamp = now.isoformat().replace("+00:00", "Z")
    assert conn._sane_rfc3339_cursor(stamp) is not None
    assert conn._sane_rfc3339_cursor("not-a-timestamp") is None
    assert conn._sane_rfc3339_cursor(None) is None


def test_rfc3339_cursor_older_than_the_floor_is_rejected():
    """Calendar's updatedMin consumes this: an implausible cursor requests years."""
    conn, _ = _probe([])
    old = datetime.now(timezone.utc) - timedelta(days=CURSOR_FLOOR_DAYS + 5)
    assert conn._sane_rfc3339_cursor(old.isoformat().replace("+00:00", "Z")) is None


def test_rfc3339_cursor_in_the_future_is_rejected():
    conn, _ = _probe([])
    far = datetime.now(timezone.utc) + timedelta(days=30)
    assert conn._sane_rfc3339_cursor(far.isoformat().replace("+00:00", "Z")) is None


def test_naive_rfc3339_cursor_is_range_checked_as_utc():
    """A tz-less stamp must still be judged on plausibility, not just parsed."""
    conn, _ = _probe([])
    old = datetime.now(timezone.utc) - timedelta(days=CURSOR_FLOOR_DAYS + 5)
    assert conn._sane_rfc3339_cursor(old.replace(tzinfo=None).isoformat()) is None
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    assert conn._sane_rfc3339_cursor(recent.replace(tzinfo=None).isoformat()) is not None


def test_implausible_rfc3339_cursor_is_logged_with_its_value(caplog):
    conn, _ = _probe([])
    old = datetime.now(timezone.utc) - timedelta(days=CURSOR_FLOOR_DAYS + 5)
    stamp = old.isoformat().replace("+00:00", "Z")
    with caplog.at_level(logging.WARNING):
        conn._sane_rfc3339_cursor(stamp)
    assert any(stamp in r.getMessage() for r in caplog.records)


# ---- BaseConnector obligations ------------------------------------------


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


def test_a_subclass_without_a_read_action_fails_at_construction():
    """test() promises a ConnectorHealth; an empty READ_ACTION would raise instead."""

    class _NoRead(GatewayConnector):
        provider = "probe"

        async def poll(self, user_id, cursor, credentials):  # pragma: no cover - unused
            return PollResult()

    with pytest.raises(ValueError) as exc:
        _NoRead(settings=None, caller=_FakeCaller([]))
    assert "READ_ACTION" in str(exc.value)


async def test_test_uses_the_read_action_and_reports_healthy():
    conn, caller = _probe([{"emailAddress": "a@b.c"}])
    health = await conn.test({})
    assert health.status == "healthy"
    assert caller.calls[0][0] == "gmail.get_profile"


async def test_test_reports_down_on_error():
    conn, _ = _probe([_raw({"status": "error", "error": "nope", "error_code": "auth_error"})])
    health = await conn.test({})
    assert health.status == "down"


async def test_test_reports_down_when_the_payload_is_unreadable():
    """A health probe must not call an unparseable response healthy."""
    conn, _ = _probe([_raw({"status": "ok", "result": "not json"})])
    health = await conn.test({})
    assert health.status == "down"
