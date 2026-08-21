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
