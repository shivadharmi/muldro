"""The runner's unit block: build the frames, fill the bodies, push the cards."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.perception_units import publish_perception_units

WHEN = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)


def _event(entity_id="t_1", minute=0):
    return SimpleNamespace(
        source="gmail",
        entity_type="email_thread",
        entity_id=entity_id,
        event_type="email_received",
        title="Series A term sheet",
        occurred_at=WHEN.replace(minute=minute),
        actor_entities={"name": "Sarah Chen"},
        importance_score=0.6,
        raw_payload={"snippet": "Can you get back to me by Friday?"},
    )


def _session():
    db = MagicMock()
    db.commit = AsyncMock()
    return db


def _db_factory(db):
    """Shaped like the runner's: the value is called, and yields a session."""

    @asynccontextmanager
    async def factory():
        yield db

    return factory


@pytest.fixture(autouse=True)
def fill():
    """The fill is the subject of its own tests and of `tests/view`.

    Every test here drives it explicitly - the default hands the units straight
    back so the frame-and-push tests are not also testing body generation, and
    so no test can reach a real model.
    """
    with patch(
        "src.orchestrator.perception_units.fill_bodies",
        AsyncMock(side_effect=lambda units, **kwargs: list(units)),
    ) as mock:
        yield mock


class _Bus:
    def __init__(self):
        self.published = []

    async def publish_to_channel(self, channel, payload):
        self.published.append((channel, payload))

    @property
    def bodies(self):
        return [json.loads(payload)["unit"]["body"] for _, payload in self.published]


class _Events:
    """Stands in for EventPublisher: the bus is reached through the accessor."""

    def __init__(self, bus=None, boom=None):
        self.bus = bus if bus is not None else _Bus()
        self.boom = boom
        self.ensure_calls = 0
        # The lazily-initialised cache the property would return. It stays None
        # so a caller that reads the property instead of awaiting the accessor
        # publishes nothing at all.
        self.event_bus = None

    async def ensure_event_bus(self):
        self.ensure_calls += 1
        if self.boom is not None:
            raise self.boom
        self.event_bus = self.bus
        return self.bus


async def _publish(raw_events, *, events, db=None, workspace_id="ws_1"):
    return await publish_perception_units(
        raw_events,
        source="gmail",
        user_id="usr_1",
        workspace_id=workspace_id,
        events=events,
        db_factory=_db_factory(db if db is not None else _session()),
    )


async def test_three_events_on_one_thread_produce_one_unit():
    events = _Events()
    units = await _publish([_event(minute=1), _event(minute=2)], events=events)
    assert len(units) == 1
    assert units[0].frame.event_count == 2


async def test_two_threads_produce_two_units():
    events = _Events()
    units = await _publish([_event(entity_id="a"), _event(entity_id="b")], events=events)
    assert len(units) == 2


async def test_no_events_is_no_units():
    events = _Events()
    assert await _publish([], events=events) == []


async def test_each_unit_is_pushed_onto_the_users_live_channel():
    events = _Events()
    await _publish([_event(entity_id="a"), _event(entity_id="b")], events=events)
    assert len(events.bus.published) == 2
    assert {channel for channel, _ in events.bus.published} == {"muldro:a2ui:usr_1"}


async def test_the_bus_comes_from_the_accessor_not_the_cached_property():
    """The cached property is None until something has awaited the accessor.

    Reading it directly would make the push a silent no-op on the first poll
    after a restart, which is the failure this asserts against.
    """
    events = _Events()
    await _publish([_event()], events=events)
    assert events.ensure_calls == 1
    assert events.bus.published


async def test_a_view_layer_bug_costs_the_diagnostic_not_the_poll():
    """A live poll must not go down because the view layer raised."""
    events = _Events()
    units = await _publish([SimpleNamespace(nothing="useful")], events=events)
    assert units == []


async def test_an_unreachable_bus_costs_the_push_not_the_poll():
    events = _Events(boom=RuntimeError("redis down"))
    units = await _publish([_event()], events=events)
    assert units == []


# --- bodies ----------------------------------------------------------------


async def test_units_come_back_with_the_bodies_the_fill_produced(fill):
    fill.side_effect = lambda units, **kwargs: [
        u.model_copy(update={"body": "prose"}) for u in units
    ]
    units = await _publish([_event()], events=_Events())
    assert [u.body for u in units] == ["prose"]


async def test_the_pushed_card_carries_the_prose(fill):
    """The push happens AFTER the fill, so the live card is the whole card.

    Pushing first would put a blank prose line on screen and leave it there
    until the user's next feed refresh - and on a steady-state poll, where
    every body is already stored and no model call is made at all, it would
    push blank over prose that exists.
    """
    fill.side_effect = lambda units, **kwargs: [u.model_copy(update={"body": "p"}) for u in units]
    events = _Events()
    await _publish([_event()], events=events)
    assert events.bus.bodies == ["p"]


async def test_the_session_is_committed_so_the_prose_survives_a_restart():
    db = _session()
    await _publish([_event()], events=_Events(), db=db)
    assert db.commit.await_count == 1


async def test_the_fill_is_told_what_each_body_would_be_written_over(fill):
    """Staleness is set inequality over event ids, so the fill needs them."""
    await _publish([_event(entity_id="a"), _event(entity_id="b")], events=_Events())
    ids_by_key = fill.await_args.kwargs["ids_by_key"]
    assert set(ids_by_key) == {"gmail:email_thread:a", "gmail:email_thread:b"}


async def test_no_events_touches_no_database(fill):
    events = _Events()
    factory = MagicMock(side_effect=AssertionError("must not open a session"))
    assert (
        await publish_perception_units(
            [],
            source="gmail",
            user_id="usr_1",
            workspace_id="ws_1",
            events=events,
            db_factory=factory,
        )
        == []
    )
    assert fill.await_count == 0


async def test_no_workspace_means_no_fill(fill):
    """A body is workspace-scoped. With no workspace there is nowhere to store
    one, and generating first and failing the write afterwards would spend real
    money to learn that."""
    factory = MagicMock(side_effect=AssertionError("must not open a session"))
    units = await publish_perception_units(
        [_event()],
        source="gmail",
        user_id="usr_1",
        workspace_id="",
        events=_Events(),
        db_factory=factory,
    )
    assert len(units) == 1
    assert fill.await_count == 0


async def test_a_fill_failure_costs_the_prose_not_the_units(fill):
    fill.side_effect = RuntimeError("db is down")
    events = _Events()
    units = await _publish([_event()], events=events)
    assert len(units) == 1
    assert units[0].body == ""
    assert events.bus.published


async def test_a_unit_whose_body_came_back_empty_is_still_a_card(fill):
    """An empty body is the repair cap's give-up - a result, not a missing
    value. Filtering on it would drop the card AND, because nothing would be
    stored, buy the same doomed generation again on every poll for ever."""
    fill.side_effect = lambda units, **kwargs: [u.model_copy(update={"body": ""}) for u in units]
    events = _Events()
    units = await _publish([_event()], events=events)
    assert len(units) == 1
    assert events.bus.bodies == [""]
