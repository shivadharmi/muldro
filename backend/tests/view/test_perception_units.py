"""Perception emits one unit per thing, not one signal per poll cycle.

perception_runner.py built ONE PerceptionSignal per poll whose summary was a
concatenation of everything in that tick. Three polls of one inbox therefore
produced three independent assessments and three cards, all titled from a
fallback constant. Grouping by frame key is what makes them one.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.view.perception import group_events_by_key


def _event(entity_id="t_1", title="Series A term sheet", minute=0, source="gmail"):
    return SimpleNamespace(
        source=source,
        entity_type="email_thread",
        entity_id=entity_id,
        event_type="email_received",
        title=title,
        occurred_at=datetime(2026, 8, 21, 14, minute, tzinfo=timezone.utc),
        actor_entities={"name": "Sarah Chen"},
        importance_score=0.5,
        raw_payload={"snippet": "Can you get back to me by Friday?"},
    )


def _event_no_timestamp(entity_id="t_1", title="Series A term sheet", source="gmail"):
    return SimpleNamespace(
        source=source,
        entity_type="email_thread",
        entity_id=entity_id,
        event_type="email_received",
        title=title,
        occurred_at=None,
        actor_entities={"name": "Sarah Chen"},
        importance_score=0.5,
        raw_payload={"snippet": "Can you get back to me by Friday?"},
    )


def test_three_events_on_one_thread_produce_one_group():
    groups = group_events_by_key([_event(minute=1), _event(minute=2), _event(minute=3)])
    assert len(groups) == 1


def test_the_group_counts_its_events():
    groups = group_events_by_key([_event(minute=1), _event(minute=2)])
    assert groups[0].event_count == 2


def test_the_group_is_keyed_on_source_entity_type_entity_id():
    groups = group_events_by_key([_event()])
    assert groups[0].key == "gmail:email_thread:t_1"


def test_the_newest_event_supplies_the_headline():
    groups = group_events_by_key(
        [_event(minute=1, title="Series A term sheet"), _event(minute=9, title="Re: term sheet")]
    )
    assert "Re: term sheet" in groups[0].latest.title


def test_distinct_threads_stay_distinct():
    groups = group_events_by_key([_event(entity_id="t_1"), _event(entity_id="t_2")])
    assert len(groups) == 2


def test_groups_are_ordered_newest_first():
    groups = group_events_by_key(
        [_event(entity_id="t_old", minute=1), _event(entity_id="t_new", minute=30)]
    )
    assert groups[0].key.endswith("t_new")


def test_empty_input_produces_no_groups():
    assert group_events_by_key([]) == []


def test_events_from_different_sources_never_merge():
    a = _event(entity_id="same", source="gmail")
    b = _event(entity_id="same", source="slack")
    assert len(group_events_by_key([a, b])) == 2


def test_one_event_missing_timestamp_among_several_does_not_raise():
    """A pre-ingest RawEvent may have no occurred_at yet. It must not crash
    the whole poll's grouping - it sorts as the oldest thing in its group."""
    dated = _event(minute=5, title="Re: term sheet")
    undated = _event_no_timestamp(title="Series A term sheet")
    groups = group_events_by_key([undated, dated])
    assert len(groups) == 1
    # The event that actually has a timestamp is newer, so it supplies the
    # headline - a missing timestamp never lets an event masquerade as latest.
    assert groups[0].latest.title == "Re: term sheet"
    assert groups[0].event_count == 2


def test_all_events_missing_timestamp_does_not_raise():
    """When every event in a group lacks occurred_at, grouping and ordering
    must still complete without raising - there is simply no real time to
    order by, so any deterministic order is acceptable."""
    a = _event_no_timestamp(entity_id="t_1", title="first")
    b = _event_no_timestamp(entity_id="t_1", title="second")
    groups = group_events_by_key([a, b])
    assert len(groups) == 1
    assert groups[0].event_count == 2
    assert groups[0].latest.title in ("first", "second")


def test_naive_and_aware_timestamps_in_one_group_do_not_raise_and_order_correctly():
    """calendar.py already hit this: naive vs aware datetimes are not
    comparable and raise TypeError. Notion's connector parses a naive
    datetime when last_edited_time lacks an offset, so a poll can hand this
    function a mix. It must not raise, and the naive timestamp must be
    treated as a real, comparable time - not silently dropped to the epoch."""
    aware = _event(minute=1, title="aware one")
    naive = _event(minute=5, title="naive one")
    naive.occurred_at = naive.occurred_at.replace(tzinfo=None)
    groups = group_events_by_key([aware, naive])
    assert len(groups) == 1
    assert groups[0].event_count == 2
    # naive was at minute=5, later than aware's minute=1, so it is latest.
    assert groups[0].latest.title == "naive one"


def test_naive_timestamp_is_treated_as_utc_not_as_missing():
    """A naive 14:00 must outrank an aware 13:00 - if a naive value were
    coerced to the epoch instead of to UTC, it would wrongly lose that
    tie-break and never be able to supply the headline."""
    earlier_aware = _event(minute=0, title="earlier aware")
    later_naive = _event(minute=0, title="later naive")
    later_naive.occurred_at = later_naive.occurred_at.replace(hour=15, minute=0, tzinfo=None)
    groups = group_events_by_key([earlier_aware, later_naive])
    assert len(groups) == 1
    assert groups[0].latest.title == "later naive"


def test_a_non_datetime_timestamp_sorts_oldest_instead_of_raising():
    """`occurred_at` comes off an external payload, so it can be a string.

    The coercion is `frame.py::ensure_aware_utc`, which calls a non-datetime
    absent; `_occurred` then gives it the same oldest-possible sentinel a
    missing one gets. Reading `.tzinfo` off the raw value instead would raise
    AttributeError inside `sorted` and cost the whole poll, not one card.
    """
    real = _event(minute=1, title="real time")
    junk = _event(minute=5, title="junk time")
    junk.occurred_at = "2026-08-21T14:05:00Z"
    groups = group_events_by_key([junk, real])
    assert len(groups) == 1
    # The junk timestamp sorts oldest, so the event that HAS a time is latest.
    assert groups[0].latest.title == "real time"
