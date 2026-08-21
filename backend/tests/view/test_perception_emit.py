"""One poll of one thread produces one Unit, not one signal per cycle."""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.view.perception import units_from_events


def _event(entity_id="t_1", title="Series A term sheet", minute=0):
    return SimpleNamespace(
        source="gmail",
        entity_type="email_thread",
        entity_id=entity_id,
        event_type="email_received",
        title=title,
        occurred_at=datetime(2026, 8, 21, 14, minute, tzinfo=timezone.utc),
        actor_entities={"name": "Sarah Chen"},
        importance_score=0.6,
        raw_payload={"snippet": "Can you get back to me by Friday?"},
    )


def test_three_events_on_one_thread_produce_one_unit():
    units = units_from_events([_event(minute=1), _event(minute=2), _event(minute=3)])
    assert len(units) == 1


def test_the_unit_reports_its_event_count():
    units = units_from_events([_event(minute=1), _event(minute=2)])
    assert units[0].frame.event_count == 2


def test_the_unit_carries_an_attributed_quote():
    units = units_from_events([_event()])
    assert units[0].quotes[0].who == "Sarah Chen"


def test_the_body_is_empty_until_the_model_writes_it():
    """frame and quotes are code's; body is the model's and is filled later."""
    assert units_from_events([_event()])[0].body == ""


def test_a_phishing_subject_produces_an_inert_headline():
    units = units_from_events([_event(title="**URGENT** [Verify](https://phish.example)")])
    headline = units[0].frame.headline
    assert "https://" not in headline
    assert "](" not in headline


def test_two_threads_produce_two_units():
    assert len(units_from_events([_event(entity_id="a"), _event(entity_id="b")])) == 2


def _raw_event(entity_id="t_1", minute=0):
    """The shape perception_runner actually holds: a pre-ingest RawEvent."""
    from src.services.event_processor import RawEvent

    return RawEvent(
        source="gmail",
        source_account_id="acct_1",
        event_type="email_received",
        entity_type="email_thread",
        entity_id=entity_id,
        occurred_at=datetime(2026, 8, 21, 14, minute, tzinfo=timezone.utc),
        title="Series A term sheet",
        actor={"type": "person", "name": "Sarah Chen"},
        raw_payload={"snippet": "Can you get back to me by Friday?"},
    )


def test_pre_ingest_raw_events_group_and_stay_attributed():
    """perception_runner builds Units from RawEvents, whose counterparty field
    is `actor`, not `actor_entities`. Reading only the latter did not raise -
    it silently produced headlines with no person and no quotes at all."""
    units = units_from_events([_raw_event(minute=1), _raw_event(minute=2)])

    assert len(units) == 1
    assert units[0].frame.headline == "Sarah Chen - Series A term sheet"
    assert units[0].frame.event_count == 2
    assert [q.who for q in units[0].quotes] == ["Sarah Chen", "Sarah Chen"]


def test_a_raw_event_has_no_importance_yet():
    """The scorer runs at ingest; a pre-ingest frame must not invent one."""
    assert units_from_events([_raw_event()])[0].frame.importance == 0.0


def test_no_events_produce_no_units():
    assert units_from_events([]) == []


def test_a_frame_that_refuses_to_build_costs_one_unit_not_the_poll():
    """Frame validates on construction - here, `source` refuses to be empty.
    One unbuildable thing must not take the rest of the poll down with it.

    A long subject used to be the likely trigger for this; it no longer is,
    because frame.py now clamps the headline rather than letting it raise.
    The fence stays: it is the class of failure that matters, not the one
    instance of it that has since been removed."""
    broken = _event(entity_id="broken")
    broken.source = ""

    units = units_from_events([broken, _event(entity_id="ok")])

    assert [u.frame.headline for u in units] == ["Sarah Chen - Series A term sheet"]


def test_a_long_subject_still_produces_a_unit():
    """The concrete regression: a 258-character subject produced zero units."""
    units = units_from_events([_event(title="Series A term sheet diligence " * 9)])

    assert len(units) == 1
    assert units[0].frame.headline
