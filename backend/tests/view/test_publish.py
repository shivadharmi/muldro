"""A Unit reaches the browser over the channel the WS route already relays.

routes_ws.py's relay_pubsub is a byte passthrough — it forwards whatever was
JSON-encoded onto muldro:a2ui:{user_id} without parsing. So publishing is the
whole backend half of the live path.
"""

import json
from datetime import datetime, timezone

from src.view.contracts import Frame, Quote, Unit
from src.view.publish import publish_units

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _unit(key="gmail:email_thread:t1") -> Unit:
    return Unit(
        frame=Frame(
            key=key,
            kind="proposal",
            status="needs_you",
            headline="Sarah Chen - Series A term sheet",
            source="gmail",
            entity_type="email_thread",
            occurred_at=NOW,
            updated_at=NOW,
            event_count=3,
        ),
        body="",
        quotes=(Quote(text="By Friday?", who="Sarah Chen", when=NOW),),
    )


class _Bus:
    def __init__(self):
        self.published = []

    async def publish_to_channel(self, channel, message):
        self.published.append((channel, message))


class _Boom:
    async def publish_to_channel(self, channel, message):
        raise RuntimeError("redis is down")


async def test_a_unit_goes_to_the_users_a2ui_channel():
    bus = _Bus()
    await publish_units(bus, [_unit()], user_id="usr_1")
    channel, _ = bus.published[0]
    assert channel == "muldro:a2ui:usr_1"


async def test_the_frame_is_a_type_unit_message():
    bus = _Bus()
    await publish_units(bus, [_unit()], user_id="usr_1")
    msg = json.loads(bus.published[0][1])
    assert msg["type"] == "unit"
    assert msg["unit"]["frame"]["key"] == "gmail:email_thread:t1"


async def test_the_message_carries_the_frames_key_at_the_top_level():
    """The WS hook guards on an identity field before dispatching. The previous
    publisher named that field differently from the field the hook read, so every
    message it sent was silently dropped while the publisher reported success."""
    bus = _Bus()
    await publish_units(bus, [_unit()], user_id="usr_1")
    msg = json.loads(bus.published[0][1])
    assert msg["key"] == "gmail:email_thread:t1"


async def test_quotes_and_counts_survive_serialization():
    bus = _Bus()
    await publish_units(bus, [_unit()], user_id="usr_1")
    unit = json.loads(bus.published[0][1])["unit"]
    assert unit["quotes"][0]["who"] == "Sarah Chen"
    assert unit["frame"]["event_count"] == 3


async def test_one_message_per_unit():
    bus = _Bus()
    await publish_units(bus, [_unit("a:b:c"), _unit("d:e:f")], user_id="usr_1")
    assert len(bus.published) == 2


async def test_no_units_publishes_nothing():
    bus = _Bus()
    await publish_units(bus, [], user_id="usr_1")
    assert bus.published == []


async def test_a_publish_failure_never_reaches_the_caller():
    """A live push failing must not take down the poll that produced it. The
    REST feed re-derives everything anyway — the Unit is not lost."""
    await publish_units(_Boom(), [_unit()], user_id="usr_1")


async def test_a_blank_user_id_publishes_nothing():
    bus = _Bus()
    await publish_units(bus, [_unit()], user_id="")
    assert bus.published == []


async def test_a_missing_bus_publishes_nothing_and_never_raises():
    """`EventPublisher.event_bus` is lazily initialised and is None until
    something has called `ensure_event_bus()`. A publisher handed None must
    fail silent rather than AttributeError into the poll — the exact class of
    "returned success and rendered nothing" bug this design exists to close, so
    it is pinned here rather than left to the caller."""
    await publish_units(None, [_unit()], user_id="usr_1")
