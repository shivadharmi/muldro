"""Redis Streams-based event bus.

Every system event flows through here. Replaces the callback-based
pattern in EventProcessor with decoupled consumer groups.

Streams:
  jarvis:events:{user_id}       — Normalized events from connectors
  jarvis:agent_events:{user_id} — Agent action events
  jarvis:system_events          — System-level events (health, budget)
  jarvis:notifications          — Notification delivery events

Consumer groups per downstream processor:
  entity_extractor, memory_extractor, planner, notifier, briefing_collector
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ulid import ULID

logger = logging.getLogger(__name__)

CONSUMER_GROUP_PREFIX = "jarvis"


@dataclass
class BusEvent:
    event_id: str
    stream: str
    event_type: str
    user_id: str
    payload: dict
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventBus:
    """Redis Streams-based event bus with consumer groups."""

    STREAM_MAXLEN = 10_000

    def __init__(self, redis):
        self._redis = redis

    async def publish(
        self,
        stream: str,
        event_type: str,
        payload: dict,
        user_id: str = "",
        metadata: dict | None = None,
    ) -> str:
        """Publish an event to a stream. Returns the event_id.

        Dual-publishes: durable via Redis Streams (``xadd``) AND real-time
        via Redis Pub/Sub (``publish``) so SSE subscribers see events
        immediately.
        """
        event_id = f"be_{ULID()}"
        created_at = datetime.now(timezone.utc).isoformat()
        data = {
            "event_id": event_id,
            "event_type": event_type,
            "user_id": user_id,
            "payload": json.dumps(payload),
            "metadata": json.dumps(metadata or {}),
            "created_at": created_at,
        }
        msg_id = await self._redis.xadd(stream, data, maxlen=self.STREAM_MAXLEN)
        logger.debug("Published %s to %s (msg=%s)", event_type, stream, msg_id)

        # Dual-publish to Pub/Sub for real-time SSE subscribers
        try:
            from src.contracts import RealtimeEventPayload

            rt = RealtimeEventPayload(
                event_id=event_id,
                event_type=event_type,
                user_id=user_id,
                payload=payload,
                metadata=metadata or {},
                created_at=created_at,
            )
            await self._redis.publish(stream, rt.model_dump_json())
        except Exception:
            logger.debug("Pub/Sub dual-publish failed for %s", event_type, exc_info=True)

        return event_id

    async def publish_to_channel(self, channel: str, data: str) -> None:
        """Publish a pre-serialized JSON string to a Redis Pub/Sub channel.

        Used for surface pushes and other real-time messages that do not
        need durable Streams storage.
        """
        await self._redis.publish(channel, data)

    async def create_consumer_group(self, stream: str, group: str, start_id: str = "0") -> None:
        """Create a consumer group if it doesn't exist."""
        try:
            await self._redis.xgroup_create(stream, group, id=start_id, mkstream=True)
            logger.info("Consumer group created: %s on %s", group, stream)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    DLQ_MAX_DELIVERIES = 3

    async def subscribe(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler: Callable[[BusEvent], Awaitable[None]],
        count: int = 10,
        block_ms: int = 5000,
    ) -> int:
        """Read and process messages from a stream. Returns count processed.

        Messages that fail DLQ_MAX_DELIVERIES times are acked and logged
        as dead-lettered to prevent infinite retry loops.
        """
        results = await self._redis.xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block_ms
        )

        processed = 0
        if results:
            for _stream_name, messages in results:
                for msg_id, data in messages:
                    try:
                        event = self._parse_event(stream, data)
                        await handler(event)
                        await self._redis.xack(stream, group, msg_id)
                        processed += 1
                    except Exception:
                        delivery_count = await self._get_delivery_count(stream, group, msg_id)
                        if delivery_count >= self.DLQ_MAX_DELIVERIES:
                            await self._redis.xack(stream, group, msg_id)
                            logger.warning(
                                "Message %s dead-lettered after %d attempts on %s/%s",
                                msg_id,
                                delivery_count,
                                stream,
                                group,
                            )
                        else:
                            logger.warning(
                                "Failed to process %s from %s (attempt %d)",
                                msg_id,
                                stream,
                                delivery_count,
                                exc_info=True,
                            )
        return processed

    async def _get_delivery_count(self, stream: str, group: str, msg_id: str) -> int:
        """Get the number of times a message has been delivered."""
        try:
            pending = await self._redis.xpending_range(stream, group, msg_id, msg_id, 1)
            if pending:
                return pending[0].get("times_delivered", 1)
        except Exception:
            pass
        return 1

    async def ack(self, stream: str, group: str, msg_id: str) -> None:
        """Acknowledge a message."""
        await self._redis.xack(stream, group, msg_id)

    async def get_pending(self, stream: str, group: str, count: int = 100) -> list[dict]:
        """Get pending (unacknowledged) messages."""
        result = await self._redis.xpending_range(stream, group, "-", "+", count)
        return [
            {
                "msg_id": entry["message_id"],
                "consumer": entry["consumer"],
                "idle_ms": entry["time_since_delivered"],
                "delivery_count": entry["times_delivered"],
            }
            for entry in result
        ]

    async def replay(
        self,
        stream: str,
        handler: Callable[[BusEvent], Awaitable[None]],
        start_id: str = "0",
        end_id: str = "+",
        count: int = 100,
    ) -> int:
        """Replay events from a stream range."""
        results = await self._redis.xrange(stream, start_id, end_id, count=count)
        processed = 0
        for _msg_id, data in results:
            event = self._parse_event(stream, data)
            await handler(event)
            processed += 1
        return processed

    async def get_stream_lag(self, stream: str) -> int:
        """Get total pending messages across all consumer groups.

        Used for backpressure detection — when lag exceeds a threshold,
        webhook endpoints should reject new events with HTTP 429.
        """
        try:
            info = await self._redis.xinfo_groups(stream)
            return sum(g.get("lag", g.get("pending", 0)) for g in info)
        except Exception:
            return 0

    def event_stream(self, user_id: str) -> str:
        """Get the events stream name for a user."""
        return f"jarvis:events:{user_id}"

    def agent_stream(self, user_id: str) -> str:
        """Get the agent events stream name for a user."""
        return f"jarvis:agent_events:{user_id}"

    @staticmethod
    def _parse_event(stream: str, data: dict) -> BusEvent:
        return BusEvent(
            event_id=data.get("event_id", ""),
            stream=stream,
            event_type=data.get("event_type", ""),
            user_id=data.get("user_id", ""),
            payload=json.loads(data.get("payload", "{}")),
            metadata=json.loads(data.get("metadata", "{}")),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(timezone.utc),
        )
