"""The runner's unit block, extracted. Same behaviour, its own home."""

from datetime import datetime, timezone
from types import SimpleNamespace

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


class _Bus:
    def __init__(self):
        self.published = []

    async def publish_to_channel(self, channel, payload):
        self.published.append((channel, payload))


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


async def test_three_events_on_one_thread_produce_one_unit():
    events = _Events()
    units = await publish_perception_units(
        [_event(minute=1), _event(minute=2)], source="gmail", user_id="usr_1", events=events
    )
    assert len(units) == 1
    assert units[0].frame.event_count == 2


async def test_two_threads_produce_two_units():
    events = _Events()
    units = await publish_perception_units(
        [_event(entity_id="a"), _event(entity_id="b")],
        source="gmail",
        user_id="usr_1",
        events=events,
    )
    assert len(units) == 2


async def test_no_events_is_no_units():
    events = _Events()
    assert await publish_perception_units([], source="gmail", user_id="usr_1", events=events) == []


async def test_each_unit_is_pushed_onto_the_users_live_channel():
    events = _Events()
    await publish_perception_units(
        [_event(entity_id="a"), _event(entity_id="b")],
        source="gmail",
        user_id="usr_1",
        events=events,
    )
    assert len(events.bus.published) == 2
    assert {channel for channel, _ in events.bus.published} == {"muldro:a2ui:usr_1"}


async def test_the_bus_comes_from_the_accessor_not_the_cached_property():
    """The cached property is None until something has awaited the accessor.

    Reading it directly would make the push a silent no-op on the first poll
    after a restart, which is the failure this asserts against.
    """
    events = _Events()
    await publish_perception_units([_event()], source="gmail", user_id="usr_1", events=events)
    assert events.ensure_calls == 1
    assert events.bus.published


async def test_a_view_layer_bug_costs_the_diagnostic_not_the_poll():
    """A live poll must not go down because the view layer raised."""
    events = _Events()
    units = await publish_perception_units(
        [SimpleNamespace(nothing="useful")], source="gmail", user_id="usr_1", events=events
    )
    assert units == []


async def test_an_unreachable_bus_costs_the_push_not_the_poll():
    events = _Events(boom=RuntimeError("redis down"))
    units = await publish_perception_units(
        [_event()], source="gmail", user_id="usr_1", events=events
    )
    assert units == []
