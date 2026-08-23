"""Which events each Unit's body was written over.

Staleness is structural, not a timer: when the set of events under a key
changes, a new message arrived and the stored prose no longer describes the
thing. That comparison is only as good as the identity function, so these
tests pin determinism as hard as they pin the grouping.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.view.perception import event_ids_by_key

WHEN = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)


def _event(entity_id="t_1", minute=0, **extra):
    fields = {
        "source": "gmail",
        "entity_type": "email_thread",
        "entity_id": entity_id,
        "event_type": "email_received",
        "title": "Series A term sheet",
        "occurred_at": WHEN.replace(minute=minute),
        "actor_entities": {"name": "Sarah Chen"},
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def test_two_events_on_one_thread_share_one_key():
    ids = event_ids_by_key([_event(minute=1), _event(minute=2)])
    assert list(ids) == ["gmail:email_thread:t_1"]
    assert len(ids["gmail:email_thread:t_1"]) == 2


def test_two_threads_produce_two_keys():
    ids = event_ids_by_key([_event(entity_id="a"), _event(entity_id="b")])
    assert set(ids) == {"gmail:email_thread:a", "gmail:email_thread:b"}


def test_a_normalized_events_own_id_wins():
    ids = event_ids_by_key([_event(event_id="evt_1", idempotency_key="gmail:t_1:m_1")])
    assert ids["gmail:email_thread:t_1"] == ("evt_1",)


def test_the_idempotency_key_is_used_when_there_is_no_row_id():
    ids = event_ids_by_key([_event(idempotency_key="gmail:t_1:m_1:email_received")])
    assert ids["gmail:email_thread:t_1"] == ("gmail:t_1:m_1:email_received",)


def test_a_pre_ingest_event_falls_back_to_a_deterministic_composite():
    """A RawEvent has neither an id nor an idempotency key - ingest mints the
    latter. The composite must still be the same string on every poll."""
    ids = event_ids_by_key([_event(minute=7)])
    first = ids["gmail:email_thread:t_1"]
    assert first == event_ids_by_key([_event(minute=7)])["gmail:email_thread:t_1"]
    assert "email_received" in first[0]


def test_two_messages_on_one_thread_get_distinct_identities():
    ids = event_ids_by_key([_event(minute=1), _event(minute=2)])
    assert len(set(ids["gmail:email_thread:t_1"])) == 2


def test_the_id_order_does_not_depend_on_arrival_order():
    """The whole point of recording ids is comparing them poll to poll.

    A connector's page order is not stable, so if the tuple followed arrival
    order the same three events would produce a different tuple on the next
    poll, every stored body would read as stale, and every poll would pay for
    a regeneration that changed nothing. Order comes from the grouping, which
    sorts oldest-first.
    """
    arrival = [_event(minute=3), _event(minute=1), _event(minute=2)]
    forwards = event_ids_by_key(arrival)["gmail:email_thread:t_1"]
    backwards = event_ids_by_key(list(reversed(arrival)))["gmail:email_thread:t_1"]
    assert forwards == backwards
    assert forwards == tuple(sorted(forwards))


def test_the_same_events_twice_produce_the_same_mapping():
    events = [_event(entity_id="a", minute=1), _event(entity_id="b", minute=2)]
    assert event_ids_by_key(events) == event_ids_by_key(events)


def test_a_non_string_identifier_is_ignored_rather_than_fatal():
    """Malformed upstream data must cost its own event, never the poll.

    A dict or an int where a string belongs is treated as absent and falls
    through to the next candidate, exactly as an empty value would.
    """
    ids = event_ids_by_key(
        [_event(event_id={"oops": 1}, idempotency_key="gmail:t_1:m_1:email_received")]
    )
    assert ids["gmail:email_thread:t_1"] == ("gmail:t_1:m_1:email_received",)


def test_an_empty_identifier_falls_through_to_the_composite():
    ids = event_ids_by_key([_event(event_id="", idempotency_key=None)])
    assert ids["gmail:email_thread:t_1"][0].startswith("gmail:t_1:email_received:")


def test_an_undatable_event_still_gets_an_identity():
    """occurred_at is absent on a pre-ingest RawEvent from some connectors, and
    can be any malformed shape at all. Neither may raise here."""
    ids = event_ids_by_key([_event(occurred_at=None), _event(entity_id="b", occurred_at="junk")])
    assert ids["gmail:email_thread:t_1"] == ("gmail:t_1:email_received:",)
    assert ids["gmail:email_thread:b"] == ("gmail:b:email_received:",)


def test_no_events_is_no_keys():
    assert event_ids_by_key([]) == {}
