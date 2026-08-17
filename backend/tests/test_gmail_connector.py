"""Gmail connector on the OpenConnector gateway.

The load-bearing fact in this file is the **message DTO shape**. OpenConnector
does NOT hand back Gmail's native object: it reshapes each message into its own
DTO. Every field name asserted below is transcribed from the recorded
``outputSchema`` for ``gmail.fetch_emails`` in
``tests/fixtures/openconnector_curated_schemas.json`` (readable via
``tests/gateway_ground_truth.py``), whose message object declares:

    required: messageId, threadId, labelIds, subject, sender, to, messageTimestamp
    optional: preview (object), payload (object|null, opaque Gmail passthrough),
              messageText (string), attachmentList (array), raw (string)
    additionalProperties: true      <- native Gmail keys MAY appear, but are
                                       NOT guaranteed
    top level: messages (required, array), nextPageToken (string|null),
               resultSizeEstimate (integer)

Note what is NOT there: ``id``, ``internalDate``, ``snippet``. A connector that
reads ``msg["id"]`` skips every real message and emits zero events — while a
fixture that invents ``id`` keeps all its tests green. This project has already
shipped seven nonexistent Gmail action ids by exactly that route, so
``_oc_message`` below builds OC's DTO and native passthrough is strictly opt-in.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.connectors.gateway_connector import OVERLAP_SECONDS
from src.connectors.gmail import (
    INITIAL_QUERY,
    MAX_BACKFILL_PAGES,
    MAX_INCREMENTAL_PAGES,
    PAGE_SIZE,
    GmailConnector,
)
from src.services.event_processor import RawEvent
from tests.conftest import TEST_USER_ID, make_mock_settings

# ---- the real transport envelope ----------------------------------------
#
# Copied from tests/connectors/test_gateway_connector.py rather than imported,
# so this file states the shape it depends on. The four hops:
#   1. OpenConnector answers {"ok": true, "data": <payload>}
#   2. src/adapter/server.py passes it through _result_to_dict
#   3. FastMCP serializes the tool return into a text block
#   4. session_pool.call_tool joins the blocks -> {"status":"ok","result": <str>}
# So ``result`` is a JSON **string**. A fake that hands back a bare dict tests a
# transport that does not exist.


@dataclass(frozen=True)
class _Raw:
    """A pre-built envelope, handed back verbatim (errors, malformed payloads)."""

    envelope: dict


def _envelope(payload: dict) -> dict:
    return {"status": "ok", "result": json.dumps({"ok": True, "data": payload})}


class _FakeCaller:
    """Substitutes GatewayToolCaller; records calls, replays queued payloads."""

    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call(self, action_id: str, payload: dict) -> dict:
        self.calls.append((action_id, dict(payload)))
        item = self._results.pop(0) if self._results else {}
        if isinstance(item, _Raw):
            return item.envelope
        return _envelope(item)


def _gmail(results: list) -> tuple[GmailConnector, _FakeCaller]:
    caller = _FakeCaller(results)
    return GmailConnector(settings=make_mock_settings(), caller=caller), caller


# ---- the OpenConnector message DTO --------------------------------------

_FIXED_TS = datetime(2026, 3, 30, 10, 0, 0, tzinfo=timezone.utc)

_DEFAULT_HEADERS = {
    "From": "alice@example.com",
    "To": "bob@example.com",
    "Cc": "carol@example.com",
    "Subject": "Follow-up",
    "Date": "Mon, 30 Mar 2026 10:00:00 -0000",
    "Message-ID": "<msg_001@mail.gmail.com>",
    "In-Reply-To": "<original@mail.gmail.com>",
    "References": "<original@mail.gmail.com> <reply1@mail.gmail.com>",
}


def _epoch_ms(when: datetime) -> str:
    return str(int(when.timestamp() * 1000))


def _oc_message(
    *,
    message_id: str = "msg_001",
    thread_id: str = "thr_001",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    subject: str = "Follow-up",
    when: datetime = _FIXED_TS,
    message_timestamp: str | None = None,
    labels: list[str] | None = None,
    headers: dict[str, str] | None = None,
    with_payload: bool = True,
    with_preview: bool = True,
    message_text: str = "Hey, following up on our call.",
    native: bool = False,
) -> dict:
    """Build one message in OpenConnector's DTO — NOT Gmail's native object.

    ``native=True`` additionally sets the native passthrough keys (``id``,
    ``internalDate``, ``snippet``). They are opt-in because the schema only says
    ``additionalProperties: true`` — it does not promise them.
    """
    header_map = dict(_DEFAULT_HEADERS)
    if headers is not None:
        header_map.update(headers)

    msg: dict = {
        # The seven keys OpenConnector's outputSchema marks required.
        "messageId": message_id,
        "threadId": thread_id,
        "labelIds": ["INBOX", "UNREAD"] if labels is None else labels,
        "subject": subject,
        "sender": sender,
        "to": to,
        # The schema types this "string" and does not specify a format.
        "messageTimestamp": (
            message_timestamp if message_timestamp is not None else _epoch_ms(when)
        ),
        "messageText": message_text,
    }
    if with_preview:
        msg["preview"] = {"text": "preview text"}
    if with_payload:
        # Opaque Gmail passthrough (additionalProperties: true, nullable).
        msg["payload"] = {"headers": [{"name": k, "value": v} for k, v in header_map.items()]}
    if native:
        msg["id"] = message_id
        msg["internalDate"] = _epoch_ms(when)
        msg["snippet"] = "native snippet"
    return msg


async def _poll_one(msg: dict, cursor: str | None = None) -> RawEvent:
    """Poll a single-message window and return the one emitted event."""
    conn, _ = _gmail([{"messages": [msg]}])
    result = await conn.poll(TEST_USER_ID, cursor, {})
    assert result.ok is True, result.error_class
    assert len(result.events) == 1
    return result.events[0]


def _recent_cursor(seconds_ago: int = 3600) -> str:
    """A plausible epoch-seconds cursor (inside GatewayConnector's sanity window)."""
    return str(int((datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).timestamp()))


# ---- THE DTO TEST: OpenConnector's names, and nothing else ---------------


async def test_openconnector_field_names_alone_produce_an_event():
    """The whole point of this task.

    A message carrying ONLY what the recorded outputSchema guarantees — no
    ``id``, no ``internalDate``, no ``snippet``, no ``payload`` — must still
    become a fully-populated RawEvent. Against a connector that reads
    ``msg["id"]`` this yields zero events with a green suite and a clean,
    empty, cursor-advancing poll: silent perception death.
    """
    msg = _oc_message(with_payload=False, with_preview=False, native=False)
    assert "id" not in msg and "internalDate" not in msg and "snippet" not in msg
    assert "payload" not in msg

    event = await _poll_one(msg)

    assert event.raw_payload["message_id"] == "msg_001"
    assert event.entity_id == "thr_001"
    assert event.title == "Follow-up"
    assert event.actor["email"] == "alice@example.com"
    assert event.raw_payload["to"] == "bob@example.com"
    assert event.raw_payload["labels"] == ["INBOX", "UNREAD"]
    assert event.occurred_at == _FIXED_TS
    # payload is an opaque passthrough — its absence must degrade, never raise.
    assert event.raw_payload["headers"] == {}


async def test_absent_payload_headers_degrade_to_an_empty_map():
    """``payload`` is nullable in the schema; a null must not raise."""
    msg = _oc_message(with_payload=False)
    msg["payload"] = None
    event = await _poll_one(msg)
    assert event.raw_payload["headers"] == {}
    assert event.raw_payload["rfc_message_id"] == ""


async def test_native_gmail_fields_are_honoured_when_they_do_pass_through():
    """additionalProperties: true — native keys MAY appear, and must be read."""
    when = datetime(2026, 2, 1, 8, 30, tzinfo=timezone.utc)
    msg = _oc_message(when=when, native=True, message_timestamp="not-a-timestamp")
    event = await _poll_one(msg)
    # internalDate (epoch millis) is preferred over the free-form messageTimestamp.
    assert event.occurred_at == when
    assert event.summary == "native snippet"


async def test_message_id_falls_back_to_the_native_id():
    msg = _oc_message(native=True)
    del msg["messageId"]
    event = await _poll_one(msg)
    assert event.raw_payload["message_id"] == "msg_001"


async def test_sender_and_subject_fall_back_to_the_headers():
    msg = _oc_message()
    msg["sender"] = ""
    msg["subject"] = ""
    event = await _poll_one(msg)
    assert event.actor["email"] == "alice@example.com"
    assert event.title == "Follow-up"


async def test_a_message_with_no_identifier_is_skipped_and_logged(caplog):
    msg = _oc_message()
    del msg["messageId"]
    conn, _ = _gmail([{"messages": [msg]}])
    with caplog.at_level(logging.WARNING, logger="src.connectors.gmail"):
        result = await conn.poll(TEST_USER_ID, None, {})
    assert result.events == []
    assert any("id" in r.getMessage() for r in caplog.records)


# ---- timestamp parsing ---------------------------------------------------


async def test_message_timestamp_parses_epoch_millis():
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    event = await _poll_one(_oc_message(message_timestamp=_epoch_ms(when), with_payload=False))
    assert event.occurred_at == when


async def test_message_timestamp_parses_epoch_seconds():
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    stamp = str(int(when.timestamp()))
    event = await _poll_one(_oc_message(message_timestamp=stamp, with_payload=False))
    assert event.occurred_at == when


async def test_message_timestamp_parses_iso_8601():
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    stamp = when.isoformat().replace("+00:00", "Z")
    event = await _poll_one(_oc_message(message_timestamp=stamp, with_payload=False))
    assert event.occurred_at == when


async def test_message_timestamp_parses_an_rfc_2822_date():
    """The schema does not specify a format, and Gmail's own Date header is RFC 2822.

    Without this branch such a payload yields occurred_at=None for every
    message, so nothing is ever observed and the cursor never advances — the
    same silent stall this connector is being rebuilt to remove.
    """
    event = await _poll_one(
        _oc_message(message_timestamp="Mon, 30 Mar 2026 10:00:00 +0000", with_payload=False)
    )
    assert event.occurred_at == _FIXED_TS


async def test_an_unparseable_timestamp_is_not_fabricated():
    """No timestamp is better than a wrong one — a fake now() poisons the watermark."""
    conn, _ = _gmail(
        [{"messages": [_oc_message(message_timestamp="whenever", with_payload=False)]}]
    )
    result = await conn.poll(TEST_USER_ID, None, {})
    assert len(result.events) == 1
    assert result.events[0].occurred_at is None
    # Nothing observable -> nothing to advance to.
    assert result.cursor is None


# ---- snippet chain -------------------------------------------------------


async def test_snippet_falls_back_to_preview_then_message_text():
    from_preview = await _poll_one(_oc_message(with_payload=False))
    assert from_preview.summary == "preview text"

    from_text = await _poll_one(
        _oc_message(with_payload=False, with_preview=False, message_text="body text")
    )
    assert from_text.summary == "body text"


async def test_summary_is_truncated():
    event = await _poll_one(
        _oc_message(with_payload=False, with_preview=False, message_text="x" * 900)
    )
    assert len(event.summary) == 500


# ---- the RawEvent contract, preserved field for field --------------------


async def test_raw_event_shape_matches_the_pre_gateway_connector():
    event = await _poll_one(_oc_message())
    assert event.source == "gmail"
    assert event.source_account_id == "gmail_primary"
    assert event.event_type == "email_received"
    assert event.entity_type == "email_thread"
    assert event.actor == {
        "type": "person",
        "email": "alice@example.com",
        "name": "alice@example.com",
    }
    rp = event.raw_payload
    assert set(rp) == {
        "message_id",
        "labels",
        "to",
        "cc",
        "rfc_message_id",
        "in_reply_to",
        "references",
        "headers",
    }
    assert rp["cc"] == "carol@example.com"
    assert rp["rfc_message_id"] == "<msg_001@mail.gmail.com>"
    assert rp["in_reply_to"] == "<original@mail.gmail.com>"
    assert rp["references"] == "<original@mail.gmail.com> <reply1@mail.gmail.com>"


async def test_entity_id_is_the_thread_id():
    """Dedup keys off (source, entity_id, message_id) — entity_id must stay the thread."""
    event = await _poll_one(_oc_message(message_id="m9", thread_id="t9"))
    assert event.entity_id == "t9"
    assert event.raw_payload["message_id"] == "m9"


async def test_bulk_mail_headers_are_still_captured():
    """triage.classify_by_rules skips newsletters with no LLM call — from these three."""
    msg = _oc_message(
        headers={
            "List-Unsubscribe": "<mailto:unsub@shop.com>",
            "List-Id": "newsletter.shop.com",
            "Precedence": "bulk",
        }
    )
    event = await _poll_one(msg)
    assert event.raw_payload["headers"] == {
        "List-Unsubscribe": "<mailto:unsub@shop.com>",
        "List-Id": "newsletter.shop.com",
        "Precedence": "bulk",
    }


async def test_only_bulk_mail_headers_are_captured():
    event = await _poll_one(_oc_message())
    assert event.raw_payload["headers"] == {}


async def test_duplicate_messages_across_pages_are_emitted_once():
    conn, _ = _gmail(
        [
            {"messages": [_oc_message(message_id="dup")], "nextPageToken": "p2"},
            {"messages": [_oc_message(message_id="dup")]},
        ]
    )
    result = await conn.poll(TEST_USER_ID, None, {})
    assert len(result.events) == 1


# ---- the request the connector actually makes ----------------------------


async def test_initial_poll_uses_the_three_day_inbox_window():
    conn, caller = _gmail([{"messages": []}])
    await conn.poll(TEST_USER_ID, None, {})
    action_id, payload = caller.calls[0]
    assert action_id == "gmail.fetch_emails"
    assert payload["query"] == INITIAL_QUERY == "is:inbox newer_than:3d"
    assert payload["detail"] == "full"
    assert payload["maxResults"] == PAGE_SIZE


async def test_incremental_poll_windows_back_by_the_overlap():
    cursor = _recent_cursor()
    conn, caller = _gmail([{"messages": []}])
    await conn.poll(TEST_USER_ID, cursor, {})
    _, payload = caller.calls[0]
    assert payload["query"] == f"after:{int(cursor) - OVERLAP_SECONDS} is:inbox"
    assert payload["detail"] == "full"


async def test_detail_is_full_because_get_message_cannot_carry_headers():
    """Settled: gmail.get_message's DTO has additionalProperties=false, seven flat
    strings, no headers and no labelIds. Falling back to it would destroy the
    List-Unsubscribe / List-Id / Precedence capture triage depends on."""
    conn, caller = _gmail([{"messages": [_oc_message()]}])
    await conn.poll(TEST_USER_ID, None, {})
    assert all(payload["detail"] == "full" for _, payload in caller.calls)
    assert all(action == "gmail.fetch_emails" for action, _ in caller.calls)


async def test_a_stale_history_id_cursor_falls_back_to_the_initial_window():
    """A Gmail historyId is a VALID 1970 epoch.

    ``after:1234567`` would sweep the entire mailbox, so the cursor must be
    rejected on plausibility and the poll must use the bounded initial window.
    """
    conn, caller = _gmail([{"messages": []}])
    await conn.poll(TEST_USER_ID, "1234567", {})
    _, payload = caller.calls[0]
    assert payload["query"] == INITIAL_QUERY
    assert "after:" not in payload["query"]


# ---- cursor policy -------------------------------------------------------


async def test_cursor_advances_to_the_max_observed_timestamp_in_seconds():
    older = datetime.now(timezone.utc) - timedelta(hours=5)
    newer = datetime.now(timezone.utc) - timedelta(hours=1)
    conn, _ = _gmail(
        [
            {
                "messages": [
                    _oc_message(message_id="a", when=older, with_payload=False),
                    _oc_message(message_id="b", when=newer, with_payload=False),
                ]
            }
        ]
    )
    result = await conn.poll(TEST_USER_ID, _recent_cursor(seconds_ago=7200), {})
    assert result.ok is True
    assert result.cursor == str(int(newer.timestamp()))


async def test_an_empty_window_keeps_the_incoming_cursor():
    """Nothing observed means nothing to advance TO; now() would skip the gap."""
    cursor = _recent_cursor()
    conn, _ = _gmail([{"messages": []}])
    result = await conn.poll(TEST_USER_ID, cursor, {})
    assert result.ok is True
    assert result.events == []
    assert result.cursor == cursor


async def test_a_truncated_walk_keeps_the_incoming_cursor():
    """THE invariant most likely to be lost in a refactor.

    Hitting the page cap with a nextPageToken still outstanding means the rest
    of the window was never read. Advancing to the newest message we happened
    to see would skip that remainder permanently and invisibly. Re-reading is
    free (EventProcessor dedups); losing the tail is not recoverable.
    """
    cursor = _recent_cursor(seconds_ago=86400)
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    pages = [
        {
            "messages": [_oc_message(message_id=f"m{i}", when=recent, with_payload=False)],
            "nextPageToken": f"p{i + 1}",
        }
        for i in range(MAX_INCREMENTAL_PAGES)
    ]
    conn, caller = _gmail(pages)
    result = await conn.poll(TEST_USER_ID, cursor, {})

    assert len(caller.calls) == MAX_INCREMENTAL_PAGES, "the page cap must bound the walk"
    assert len(result.events) == MAX_INCREMENTAL_PAGES, "what WAS read is still delivered"
    assert result.ok is True
    assert result.cursor == cursor, (
        "a truncated walk must NOT advance the cursor — the undrained remainder "
        "would be skipped forever"
    )


async def test_the_initial_backfill_is_capped_separately():
    pages = [
        {"messages": [_oc_message(message_id=f"m{i}", with_payload=False)], "nextPageToken": "next"}
        for i in range(MAX_BACKFILL_PAGES + 3)
    ]
    conn, caller = _gmail(pages)
    result = await conn.poll(TEST_USER_ID, None, {})
    assert len(caller.calls) == MAX_BACKFILL_PAGES
    assert result.cursor is None, "a truncated backfill holds the (absent) incoming cursor"


async def test_a_failed_poll_keeps_the_incoming_cursor_and_classifies():
    cursor = _recent_cursor()
    conn, _ = _gmail([_Raw({"status": "error", "error": "slow down", "error_code": "rate_limit"})])
    result = await conn.poll(TEST_USER_ID, cursor, {})
    assert result.failed is True
    assert result.error_class == "rate_limited"
    assert result.cursor == cursor
    assert result.events == []


async def test_a_shape_mismatch_is_a_failure_not_an_empty_mailbox():
    """``messages`` is required by the outputSchema, so its absence is a mismatch."""
    cursor = _recent_cursor()
    conn, _ = _gmail([_Raw({"status": "ok", "result": json.dumps({"ok": True, "junk": 1})})])
    result = await conn.poll(TEST_USER_ID, cursor, {})
    assert result.failed is True
    assert result.cursor == cursor


async def test_a_missing_caller_is_a_failure_not_an_empty_poll():
    """Perception died silently once already because a dependency was not injected."""
    conn = GmailConnector(settings=make_mock_settings(), caller=None)
    cursor = _recent_cursor()
    result = await conn.poll(TEST_USER_ID, cursor, {})
    assert result.failed is True
    assert result.cursor == cursor


# ---- wiring --------------------------------------------------------------


def test_connector_declares_the_gateway_contract():
    conn, _ = _gmail([])
    assert conn.provider == "gmail"
    assert conn.cursor_type == "timestamp"
    assert conn.READ_ACTION == "gmail.get_profile"
    assert conn.FETCH_ACTION == "gmail.fetch_emails"
    assert conn.supports_actions is False, "writes go through the gateway, not the connector"


async def test_health_probe_uses_the_gateway_read_action():
    conn, caller = _gmail([{"emailAddress": "a@b.c"}])
    health = await conn.test({})
    assert health.status == "healthy"
    assert caller.calls[0][0] == "gmail.get_profile"
