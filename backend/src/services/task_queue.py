"""Redis Streams-based async task processing.

Provides async event processing pipeline so webhook responses return
immediately while callbacks (entity extraction, memory extraction,
proactive planning) run in the background.
"""

import json
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "muldro-workers"
CONSUMER_NAME = "worker-1"


class TaskQueue:
    """Redis Streams-based async task processing."""

    def __init__(self, redis):
        self._redis = redis

    async def ensure_group(self, stream: str) -> None:
        """Create consumer group if it doesn't exist."""
        try:
            await self._redis.xgroup_create(stream, CONSUMER_GROUP, id="0", mkstream=True)
        except Exception as exc:
            # Group already exists
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, stream: str, task_type: str, payload: dict) -> str:
        """Add task to stream. Returns message ID."""
        data = {"task_type": task_type, "payload": json.dumps(payload)}
        msg_id = await self._redis.xadd(stream, data, maxlen=10_000)
        logger.debug("Enqueued %s to %s: %s", task_type, stream, msg_id)
        return msg_id

    async def read_pending(
        self, stream: str, count: int = 10, block_ms: int = 5000
    ) -> list[tuple[str, dict]]:
        """Read pending messages from the stream. Returns list of (msg_id, data)."""
        results = await self._redis.xreadgroup(
            CONSUMER_GROUP,
            CONSUMER_NAME,
            {stream: ">"},
            count=count,
            block=block_ms,
        )

        messages = []
        if results:
            for _stream_name, stream_messages in results:
                for msg_id, data in stream_messages:
                    parsed = {
                        "task_type": data.get("task_type", ""),
                        "payload": json.loads(data.get("payload", "{}")),
                    }
                    messages.append((msg_id, parsed))

        return messages

    async def ack(self, stream: str, msg_id: str) -> None:
        """Acknowledge a processed message."""
        await self._redis.xack(stream, CONSUMER_GROUP, msg_id)
        logger.debug("ACK %s from %s", msg_id, stream)

    async def process_stream(
        self,
        stream: str,
        handler: Callable[[str, dict], Awaitable[None]],
        count: int = 10,
        block_ms: int = 5000,
    ) -> int:
        """Read and process messages from a stream. Returns number processed."""
        messages = await self.read_pending(stream, count=count, block_ms=block_ms)
        processed = 0

        for msg_id, data in messages:
            try:
                await handler(data["task_type"], data["payload"])
                await self.ack(stream, msg_id)
                processed += 1
            except Exception:
                logger.warning(
                    "Failed to process %s from %s: %s",
                    msg_id,
                    stream,
                    data.get("task_type"),
                    exc_info=True,
                )

        return processed
