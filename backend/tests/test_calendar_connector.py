"""Google Calendar connector — gateway window building, normalization, cursor policy.

The connector no longer speaks Google REST: it calls ``googlecalendar.list_events``
through the OpenConnector gateway. The old httpx/``syncToken``/410 tests are gone
with that transport — a ``syncToken`` cursor was rejected because an expired token
arrives here as an opaque error, not an HTTP 410, so the connector could not tell
"resync now" from "retry later". A timestamp cursor cannot expire.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.connectors.calendar import MAX_PAGES, PAGE_SIZE, CalendarConnector
from src.connectors.gateway_connector import CURSOR_FLOOR_DAYS, OVERLAP_SECONDS
from tests.conftest import TEST_USER_ID, make_mock_settings

LIST_ACTION = "googlecalendar.list_events"


# ---- the real transport envelope ----------------------------------------
#
# Shape copied from tests/connectors/test_gateway_connector.py, which derives it
# from the four hops: OpenConnector answers {"ok": true, "data": <payload>}, the
# adapter passes it through, FastMCP serializes it to a text block, and
# session_pool.call_tool returns {"status": "ok", "result": <JSON string>}. A
# fake that hands back a bare dict asserts a shape that does not exist.


@dataclass(frozen=True)
class _Raw:
    """A pre-built MCP envelope, handed back verbatim."""

    envelope: dict


def _raw(envelope: dict) -> _Raw:
    return _Raw(envelope)


def _envelope(payload: dict) -> dict:
    return {"status": "ok", "result": json.dumps({"ok": True, "data": payload})}


class _FakeCaller:
    """Substitutes GatewayToolCaller; records calls and replays queued payloads."""

    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call(self, action_id: str, payload: dict) -> dict:
        self.calls.append((action_id, dict(payload)))
        item = self._results.pop(0) if self._results else {}
        if isinstance(item, _Raw):
            return item.envelope
        return _envelope(item)


def _connector(results: list) -> tuple[CalendarConnector, _FakeCaller]:
    caller = _FakeCaller(results)
    return CalendarConnector(make_mock_settings(), caller=caller), caller


def _rfc3339(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _event(
    event_id: str,
    summary: str = "Meeting",
    updated: str = "2026-06-21T09:00:00.000Z",
) -> dict:
    """A Google Calendar event as ``googlecalendar.list_events`` returns it.

    The recorded outputSchema for this action is PASSTHROUGH-shaped: top level
    is items/nextPageToken/nextSyncToken/timeZone/updated with required=["items"],
    and each row carries Google's native field names (id, status, summary, start,
    end, organizer, attendees, updated, ...) with additionalProperties: true.
    So Google's own field names are the correct fixture here.
    """
    return {
        "id": event_id,
        "status": "confirmed",
        "summary": summary,
        "updated": updated,
        "start": {"dateTime": "2026-06-21T10:00:00Z"},
        "end": {"dateTime": "2026-06-21T11:00:00Z"},
        "organizer": {"email": "alice@example.com", "displayName": "Alice"},
        "attendees": [],
    }


# ---- window building -----------------------------------------------------


async def test_initial_poll_asks_for_the_calendar_from_now_not_a_change_feed():
    """cursor=None answers "what is on my calendar": timeMin, singleEvents, no updatedMin."""
    connector, caller = _connector([{"items": []}])

    before = datetime.now(timezone.utc)
    await connector.poll(TEST_USER_ID, None, {})
    after = datetime.now(timezone.utc)

    action_id, payload = caller.calls[0]
    assert action_id == LIST_ACTION
    assert payload["calendarId"] == "primary"
    assert payload["singleEvents"] is True
    assert payload["maxResults"] == PAGE_SIZE
    assert "timeMin" in payload
    assert "updatedMin" not in payload, "an initial sync is not a change feed"
    assert before <= _parse(payload["timeMin"]) <= after


async def test_incremental_poll_asks_for_changes_with_an_overlapping_updatedmin():
    """A cursor answers "what changed": updatedMin re-read by OVERLAP_SECONDS, no timeMin."""
    connector, caller = _connector([{"items": []}])

    cursor_at = datetime.now(timezone.utc) - timedelta(hours=3)
    cursor = _rfc3339(cursor_at)

    await connector.poll(TEST_USER_ID, cursor, {})

    _, payload = caller.calls[0]
    assert "timeMin" not in payload, "an incremental sync must not re-ask for the whole calendar"
    expected = cursor_at - timedelta(seconds=OVERLAP_SECONDS)
    assert abs(_parse(payload["updatedMin"]) - expected) <= timedelta(seconds=2)


async def test_showdeleted_is_never_sent():
    """updatedMin already forces deletions in, and setting it changes initial-sync semantics.

    Google: "When specified [updatedMin], entries deleted since this time will
    always be included regardless of showDeleted."
    """
    connector, caller = _connector([{"items": []}, {"items": []}])
    await connector.poll(TEST_USER_ID, None, {})
    await connector.poll(
        TEST_USER_ID, _rfc3339(datetime.now(timezone.utc) - timedelta(hours=1)), {}
    )

    assert all("showDeleted" not in payload for _, payload in caller.calls)


async def test_synctoken_is_never_sent_in_either_mode():
    """syncToken exists in the inputSchema and is deliberately unused.

    Through adapter -> OpenConnector -> Google the connector sees a result dict,
    not an HTTP 410, so an expired token is indistinguishable from a transient
    error: the failure modes are stall-forever or full-resync-every-tick.
    """
    connector, caller = _connector([{"items": []}, {"items": []}])

    await connector.poll(TEST_USER_ID, None, {})
    await connector.poll(
        TEST_USER_ID, _rfc3339(datetime.now(timezone.utc) - timedelta(hours=1)), {}
    )

    assert len(caller.calls) == 2
    assert all("syncToken" not in payload for _, payload in caller.calls)


async def test_a_stale_synctoken_shaped_cursor_falls_back_to_the_initial_window():
    """The pre-gateway cursor is an opaque base64 blob; it must not become updatedMin."""
    connector, caller = _connector([{"items": []}])

    result = await connector.poll(TEST_USER_ID, "CPDAlvWDx70CEPDAlvWDx70CGAU=", {})

    _, payload = caller.calls[0]
    assert "timeMin" in payload
    assert "updatedMin" not in payload
    assert "syncToken" not in payload
    assert result.ok is True


# ---- normalization -------------------------------------------------------


async def test_events_normalize_across_pages():
    connector, _ = _connector(
        [
            {
                "items": [_event("evt_1", "Standup"), _event("evt_2", "Design review")],
                "nextPageToken": "p2",
            },
            {"items": [_event("evt_3", "Retro")]},
        ]
    )

    result = await connector.poll(TEST_USER_ID, None, {})

    assert result.ok is True
    assert {e.entity_id for e in result.events} == {"evt_1", "evt_2", "evt_3"}
    event = next(e for e in result.events if e.entity_id == "evt_1")
    assert event.source == "calendar"
    assert event.entity_type == "meeting"
    assert event.event_type == "event_created"
    assert event.title == "Standup"
    assert event.actor["email"] == "alice@example.com"
    assert event.occurred_at is not None
    assert event.occurred_at.tzinfo is not None


async def test_a_cancelled_event_still_maps_to_event_cancelled():
    """Deletions are delivered: updatedMin always includes entries deleted since it."""
    connector, _ = _connector(
        [{"items": [{"id": "evt_gone", "status": "cancelled", "updated": "2026-06-21T09:30:00Z"}]}]
    )

    result = await connector.poll(TEST_USER_ID, None, {})

    assert result.ok is True
    assert len(result.events) == 1
    assert result.events[0].entity_id == "evt_gone"
    assert result.events[0].event_type == "event_cancelled"
    assert result.events[0].occurred_at is None


async def test_an_all_day_event_yields_a_utc_aware_occurred_at():
    """start.date parses NAIVE; mixed naive/aware occurred_at raises downstream."""
    connector, _ = _connector(
        [
            {
                "items": [
                    {
                        "id": "evt_allday",
                        "status": "confirmed",
                        "summary": "Company holiday",
                        "updated": "2026-06-21T09:00:00Z",
                        "start": {"date": "2026-06-25"},
                        "end": {"date": "2026-06-26"},
                    }
                ]
            }
        ]
    )

    result = await connector.poll(TEST_USER_ID, None, {})

    occurred_at = result.events[0].occurred_at
    assert occurred_at is not None
    assert occurred_at.tzinfo is not None
    assert occurred_at.utcoffset() == timedelta(0)
    assert (occurred_at.year, occurred_at.month, occurred_at.day) == (2026, 6, 25)


# ---- cursor policy -------------------------------------------------------


async def test_the_cursor_advances_to_the_max_observed_updated():
    """On the INCREMENTAL branch (a real cursor in), the cursor advances to max(updated).

    The initial branch (cursor=None) does NOT use max(updated) as its seed — see
    test_an_initial_sync_seeds_poll_start_even_when_events_are_observed below for
    why (a future timeMin=now window can return rows with an ancient `updated`,
    which would pin the connector in initial mode forever).
    """
    incoming = _rfc3339(datetime.now(timezone.utc) - timedelta(hours=2))
    connector, _ = _connector(
        [
            {
                "items": [
                    _event("evt_old", updated="2026-06-21T09:00:00.000Z"),
                    _event("evt_new", updated="2026-06-21T12:30:00.000Z"),
                    _event("evt_mid", updated="2026-06-21T11:00:00.000Z"),
                ]
            }
        ]
    )

    result = await connector.poll(TEST_USER_ID, incoming, {})

    assert result.cursor == "2026-06-21T12:30:00.000Z"


async def test_rows_without_a_usable_updated_stamp_hold_the_incoming_cursor():
    """``updated`` is optional in the recorded outputSchema (required is id + status).

    An empty or missing stamp is not a watermark. Taking it would make the next
    poll reject the cursor and restart from now(), skipping the window between.
    """
    incoming = _rfc3339(datetime.now(timezone.utc) - timedelta(hours=2))
    connector, _ = _connector(
        [
            {
                "items": [
                    {"id": "evt_no_stamp", "status": "confirmed"},
                    {"id": "evt_blank_stamp", "status": "confirmed", "updated": ""},
                ]
            }
        ]
    )

    result = await connector.poll(TEST_USER_ID, incoming, {})

    assert result.ok is True
    assert len(result.events) == 2, "the events are still ingested"
    assert result.cursor == incoming


async def test_a_truncated_walk_keeps_the_incoming_cursor():
    """The remaining window was NOT read; advancing would skip it permanently.

    Re-polling the same window is cheap (EventProcessor dedups); losing the tail
    is not recoverable. This must be resolved through _resolve_cursor, not by
    reading walk.truncated inline.
    """
    incoming = _rfc3339(datetime.now(timezone.utc) - timedelta(hours=2))
    # Every page offers another page token, so the walk stops at MAX_PAGES with
    # the provider still holding data — and every page carries a NEWER watermark
    # than the incoming cursor, so an unguarded connector would happily advance.
    pages = [
        {
            "items": [_event(f"evt_{i}", updated="2026-06-21T23:59:00.000Z")],
            "nextPageToken": f"p{i}",
        }
        for i in range(MAX_PAGES)
    ]
    connector, caller = _connector(pages)

    result = await connector.poll(TEST_USER_ID, incoming, {})

    assert len(caller.calls) == MAX_PAGES
    assert result.ok is True
    assert result.events, "a truncated walk still yields the pages it did read"
    assert result.cursor == incoming, (
        "advancing past an undrained window skips the remainder permanently"
    )


async def test_an_empty_window_holds_the_incoming_cursor():
    """Nothing observed means nothing to advance TO; now() would skip the gap."""
    incoming = _rfc3339(datetime.now(timezone.utc) - timedelta(hours=2))
    connector, _ = _connector([{"items": []}])

    result = await connector.poll(TEST_USER_ID, incoming, {})

    assert result.ok is True
    assert result.events == []
    assert result.cursor == incoming


# ---- leaving initial-sync mode -------------------------------------------
#
# Before the gateway port, Google returned a nextSyncToken even on an empty
# final page, so the connector always advanced out of initial mode. Through the
# gateway there is no such token, so an empty initial sync observed nothing,
# held its (absent) cursor, and asked timeMin=now again on the next poll —
# forever. While stuck there a newly-created PAST-dated event is invisible:
# timeMin=now excludes it and updatedMin never runs.


async def test_an_empty_initial_sync_seeds_a_cursor_at_poll_start_minus_the_overlap():
    """An initial sync is not a change feed, so poll-start is a safe seed.

    The "never advance to now(), only to max-observed" invariant guards a change
    feed, where anything modified between the last row and now() would be
    skipped. Here the walk asked "what is on my calendar" (timeMin) — the next
    poll's updatedMin covers everything modified since poll-start, so nothing is
    skipped. Poll-start is captured BEFORE the request; post-walk now() would
    skip whatever was modified during the walk.
    """
    connector, _ = _connector([{"items": []}])

    before = datetime.now(timezone.utc)
    result = await connector.poll(TEST_USER_ID, None, {})
    after = datetime.now(timezone.utc)

    assert result.ok is True
    assert result.cursor is not None, "an empty initial sync must not stay in initial mode"
    seeded = _parse(result.cursor)
    assert before - timedelta(seconds=OVERLAP_SECONDS) <= seeded
    assert seeded <= after - timedelta(seconds=OVERLAP_SECONDS)


async def test_the_poll_after_an_empty_initial_sync_asks_updatedmin_not_timemin():
    """The seed must be a cursor the NEXT poll accepts — the whole point."""
    connector, caller = _connector([{"items": []}, {"items": []}])

    first = await connector.poll(TEST_USER_ID, None, {})
    await connector.poll(TEST_USER_ID, first.cursor, {})

    _, second_payload = caller.calls[1]
    assert "updatedMin" in second_payload, "the seeded cursor must survive the read clamp"
    assert "timeMin" not in second_payload


async def test_an_initial_sync_seeds_poll_start_even_when_events_are_observed():
    """The pin: ``timeMin=now`` can return rows whose ``updated`` is ancient.

    A long-established recurring series may not have been touched in years, so
    ``max(updated)`` over an initial sync's rows can sit well outside
    ``CURSOR_FLOOR_DAYS``. Seeding that value would write a cursor the very next
    poll's plausibility check rejects, bouncing back to ``timeMin=now`` with the
    same rows returned again -> the same stale cursor written again, forever: an
    initial sync that never leaves initial mode even though it observes events on
    every poll. Two consecutive polls are run so the pin (or its absence) is what
    is actually measured, not just one field.
    """
    stale_updated = _rfc3339(datetime.now(timezone.utc) - timedelta(days=CURSOR_FLOOR_DAYS + 30))
    connector, caller = _connector(
        [
            {"items": [_event("evt_recurring", updated=stale_updated)]},
            {"items": [_event("evt_recurring", updated=stale_updated)]},
        ]
    )

    before = datetime.now(timezone.utc)
    first = await connector.poll(TEST_USER_ID, None, {})
    after = datetime.now(timezone.utc)

    first_action, first_payload = caller.calls[0]
    assert first_action == LIST_ACTION
    assert "timeMin" in first_payload, "poll 1: initial window"
    assert first.ok is True
    assert first.cursor is not None
    assert first.cursor != stale_updated, "max(updated) must not become the seed"
    seeded = _parse(first.cursor)
    assert before - timedelta(seconds=OVERLAP_SECONDS) <= seeded
    assert seeded <= after - timedelta(seconds=OVERLAP_SECONDS)

    second = await connector.poll(TEST_USER_ID, first.cursor, {})

    _, second_payload = caller.calls[1]
    assert "updatedMin" in second_payload, (
        "poll 2: a plausible seed must survive the read clamp — a stale seed "
        "would be rejected and bounce back to timeMin=now"
    )
    assert "timeMin" not in second_payload
    assert second.ok is True


async def test_an_empty_incremental_poll_does_not_seed_and_holds_its_cursor():
    """Seeding is the INITIAL branch only; a change feed must still hold."""
    incoming = _rfc3339(datetime.now(timezone.utc) - timedelta(hours=2))
    connector, _ = _connector([{"items": []}])

    result = await connector.poll(TEST_USER_ID, incoming, {})

    assert result.cursor == incoming


async def test_a_truncated_initial_walk_does_not_seed():
    """Seeding past an undrained window is the bug _resolve_cursor exists to stop."""
    pages = [
        {
            "items": [_event(f"evt_{i}", updated="2026-06-21T23:59:00.000Z")],
            "nextPageToken": f"p{i}",
        }
        for i in range(MAX_PAGES)
    ]
    connector, caller = _connector(pages)

    result = await connector.poll(TEST_USER_ID, None, {})

    assert len(caller.calls) == MAX_PAGES
    assert result.ok is True
    assert result.cursor is None, "a truncated initial walk holds the (absent) incoming cursor"


async def test_a_failed_poll_keeps_the_incoming_cursor_and_classifies_transient():
    incoming = _rfc3339(datetime.now(timezone.utc) - timedelta(hours=2))
    connector, _ = _connector(
        [_raw({"status": "error", "error": "upstream down", "error_code": "server_error"})]
    )

    result = await connector.poll(TEST_USER_ID, incoming, {})

    assert result.failed is True
    assert result.error_class == "transient"
    assert result.cursor == incoming
    assert result.events == []


async def test_a_rate_limited_poll_is_classified_as_rate_limited():
    connector, _ = _connector(
        [_raw({"status": "error", "error": "slow down", "error_code": "rate_limit"})]
    )

    result = await connector.poll(TEST_USER_ID, None, {})

    assert result.error_class == "rate_limited"
    assert result.cursor is None


async def test_a_shape_mismatch_is_a_failure_not_an_empty_calendar():
    """The recorded outputSchema lists ``items`` as required, so its absence is a mismatch."""
    incoming = _rfc3339(datetime.now(timezone.utc) - timedelta(hours=2))
    connector, _ = _connector([_raw({"status": "ok", "result": json.dumps({"ok": True})})])

    result = await connector.poll(TEST_USER_ID, incoming, {})

    assert result.failed is True
    assert result.cursor == incoming
    assert result.events == []


async def test_a_missing_caller_fails_loudly_rather_than_reporting_an_empty_calendar():
    """Perception died silently once already; an uninjected transport must not look clean."""
    incoming = _rfc3339(datetime.now(timezone.utc) - timedelta(hours=2))
    connector = CalendarConnector(make_mock_settings())

    result = await connector.poll(TEST_USER_ID, incoming, {})

    assert result.failed is True
    assert result.error_class == "transient"
    assert result.cursor == incoming


# ---- BaseConnector obligations ------------------------------------------


async def test_test_probes_with_the_cheap_read_action():
    connector, caller = _connector([{"items": []}])

    health = await connector.test({})

    assert health.status == "healthy"
    assert caller.calls[0][0] == "googlecalendar.list_calendars"
