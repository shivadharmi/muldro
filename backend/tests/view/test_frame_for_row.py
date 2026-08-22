"""A Frame built from a domain row runs the same neutralizer as one built from an event.

Every field other than `headline` rests on there being exactly one
construction site per origin. `frame_for_event` is that site for
perception; `frame_for_row` is it for muldro's own rows. Both must neutralize
through `_plain` + `_clamp_headline`, or a briefing headline carrying `**`
raises inside Frame's validator and the founder loses the card.
"""

from datetime import datetime, timezone

from src.view.contracts import Affordance
from src.view.frame import frame_for_row


def _row(**overrides):
    defaults = dict(
        source="muldro",
        entity_type="run",
        entity_id="run_01ABC",
        kind="run",
        status="running",
        headline="Draft the investor update",
        occurred_at=datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return frame_for_row(**defaults)


def test_the_key_is_source_entity_type_entity_id():
    assert _row().key == "muldro:run:run_01ABC"


def test_a_markdown_headline_is_neutralized_rather_than_refused():
    frame = _row(headline="**Board pack** ready — see [the doc](https://x.example)")
    assert "**" not in frame.headline
    assert "https://" not in frame.headline
    assert frame.headline


def test_an_unusable_headline_falls_back_to_what_muldro_knows():
    """Never a constant like 'New activity' — that is what made three cards alike."""
    assert _row(headline="   ").headline == "muldro run"


def test_updated_at_defaults_to_occurred_at():
    frame = _row()
    assert frame.updated_at == frame.occurred_at


def test_both_timestamps_go_through_one_normalizer():
    """A naive value is assumed UTC, never left naive beside an aware sibling."""
    frame = _row(
        occurred_at=datetime(2026, 8, 22, 9, 0),
        updated_at=datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc),
    )
    assert frame.occurred_at.tzinfo is not None
    assert frame.updated_at.tzinfo is not None


def test_a_missing_occurred_at_does_not_raise():
    assert _row(occurred_at=None).occurred_at.tzinfo is not None


def test_affordances_are_carried():
    frame = _row(
        affordances=[Affordance(capability="internal.get_run", label="Open", variant="primary")]
    )
    assert frame.affordances[0].label == "Open"


def test_importance_defaults_to_zero():
    """rank() returns an order, not a score, so nothing fills this."""
    assert _row().importance == 0.0
