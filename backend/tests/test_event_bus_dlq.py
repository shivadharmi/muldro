"""Tests for EventBus dead-letter / pending-reclaim semantics (P1 #1).

These use a *stateful* fake Redis that models the consumer-group subset we
rely on (xadd / xreadgroup ">" / xack / xpending_range / xautoclaim) so the
tests exercise the real reclaim+dead-letter logic rather than a scripted mock.
"""

import json
from datetime import datetime, timezone

import pytest

from src.services.event_bus import EventBus


def _entry(event_type: str = "test", **payload) -> dict:
    return {
        "event_id": payload.get("event_id", "be_x"),
        "event_type": event_type,
        "user_id": payload.get("user_id", "usr_1"),
        "payload": json.dumps(payload or {}),
        "metadata": "{}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


class FakeStreamRedis:
    """Minimal stateful Redis Streams + consumer-group double.

    Models one delivery per ``xreadgroup``/``xautoclaim`` call. ``xautoclaim``
    reclaims all currently-pending entries (idle time is treated as elapsed
    between calls) and increments their delivery counts, mirroring how a
    looping consumer eventually re-sees its own failed messages.
    """

    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        # (stream, group) -> {"last": int, "pending": {msg_id: {data, count, consumer}}}
        self.groups: dict[tuple[str, str], dict] = {}
        self._seq = 0

    async def xadd(self, stream, data, maxlen=None):
        self._seq += 1
        msg_id = f"{self._seq}-0"
        self.streams.setdefault(stream, []).append((msg_id, dict(data)))
        return msg_id

    async def xgroup_create(self, stream, group, id="0", mkstream=True):
        self.groups.setdefault((stream, group), {"last_index": 0, "pending": {}})

    async def xreadgroup(self, group, consumer, streams, count=10, block=None):
        out = []
        for stream in streams:
            g = self.groups.setdefault((stream, group), {"last_index": 0, "pending": {}})
            msgs = self.streams.get(stream, [])
            new = msgs[g["last_index"] : g["last_index"] + count]
            delivered = []
            for msg_id, data in new:
                g["pending"][msg_id] = {"data": data, "count": 1, "consumer": consumer}
                delivered.append((msg_id, data))
            g["last_index"] += len(new)
            if delivered:
                out.append((stream, delivered))
        return out

    async def xautoclaim(self, stream, group, consumer, min_idle_time, start_id="0-0", count=10):
        g = self.groups.setdefault((stream, group), {"last_index": 0, "pending": {}})
        claimed = []
        for msg_id, entry in list(g["pending"].items())[:count]:
            entry["count"] += 1
            entry["consumer"] = consumer
            claimed.append((msg_id, entry["data"]))
        return ("0-0", claimed, [])

    async def xack(self, stream, group, msg_id):
        g = self.groups.get((stream, group))
        if g:
            g["pending"].pop(msg_id, None)

    async def xpending_range(self, stream, group, start, end, count):
        g = self.groups.get((stream, group))
        if not g:
            return []
        result = []
        for msg_id, entry in g["pending"].items():
            if start != "-" and end != "+" and not (start <= msg_id <= end):
                continue
            result.append(
                {
                    "message_id": msg_id,
                    "consumer": entry["consumer"],
                    "time_since_delivered": 999_999,
                    "times_delivered": entry["count"],
                }
            )
        return result

    async def publish(self, *a, **k):
        return 0

    def pending_count(self, stream, group) -> int:
        g = self.groups.get((stream, group))
        return len(g["pending"]) if g else 0


@pytest.fixture
def fake_redis():
    return FakeStreamRedis()


@pytest.fixture
def bus(fake_redis):
    return EventBus(fake_redis)


async def _drain(bus, stream, group, handler, on_dead_letter=None, rounds=10):
    """Run subscribe() repeatedly to simulate the consumer loop."""
    total = 0
    for _ in range(rounds):
        total += await bus.subscribe(
            stream, group, "consumer-1", handler, on_dead_letter=on_dead_letter
        )
    return total


class TestDeadLetterReclaim:
    async def test_always_failing_message_is_dead_lettered_after_max_deliveries(
        self, bus, fake_redis
    ):
        stream, group = "jarvis:events:usr_1", "entity_extractor"
        await bus.create_consumer_group(stream, group)
        await fake_redis.xadd(stream, _entry())

        attempts = {"n": 0}

        async def handler(event):
            attempts["n"] += 1
            raise RuntimeError("boom")

        dead_lettered = []

        async def on_dead_letter(ctx):
            dead_lettered.append(ctx)

        await _drain(bus, stream, group, handler, on_dead_letter=on_dead_letter)

        # Tried exactly DLQ_MAX_DELIVERIES times, then dead-lettered + acked.
        assert attempts["n"] == EventBus.DLQ_MAX_DELIVERIES
        assert len(dead_lettered) == 1
        assert fake_redis.pending_count(stream, group) == 0

    async def test_transient_failure_recovers_on_reclaim(self, bus, fake_redis):
        stream, group = "jarvis:events:usr_1", "memory_extractor"
        await bus.create_consumer_group(stream, group)
        await fake_redis.xadd(stream, _entry())

        attempts = {"n": 0}

        async def handler(event):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("transient")

        dead_lettered = []

        async def on_dead_letter(ctx):
            dead_lettered.append(ctx)

        await _drain(bus, stream, group, handler, on_dead_letter=on_dead_letter)

        assert attempts["n"] == 2  # failed once, succeeded on reclaim
        assert dead_lettered == []
        assert fake_redis.pending_count(stream, group) == 0

    async def test_dead_letter_context_carries_event_and_error(self, bus, fake_redis):
        stream, group = "jarvis:events:usr_1", "trigger_evaluator"
        await bus.create_consumer_group(stream, group)
        await fake_redis.xadd(stream, _entry(event_type="email_received", event_id="be_42"))

        async def handler(event):
            raise ValueError("nope")

        captured = []

        async def on_dead_letter(ctx):
            captured.append(ctx)

        await _drain(bus, stream, group, handler, on_dead_letter=on_dead_letter)

        assert len(captured) == 1
        ctx = captured[0]
        assert ctx.stream == stream
        assert ctx.group == group
        assert ctx.delivery_count >= EventBus.DLQ_MAX_DELIVERIES
        assert ctx.data.get("event_id") == "be_42"
        assert isinstance(ctx.error, ValueError)
