"""Tests for TaskQueue using fakeredis."""

import pytest

from src.services.task_queue import TaskQueue

try:
    import fakeredis.aioredis as fakeredis_aio
except ImportError:
    fakeredis_aio = None


@pytest.fixture
async def redis():
    if fakeredis_aio is None:
        pytest.skip("fakeredis not installed")
    r = fakeredis_aio.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def queue(redis):
    return TaskQueue(redis)


@pytest.mark.asyncio
async def test_enqueue_returns_message_id(queue):
    """enqueue should return a Redis stream message ID."""
    await queue.ensure_group("test:stream")
    msg_id = await queue.enqueue("test:stream", "entity_extraction", {"event_id": "evt_1"})
    assert msg_id is not None


@pytest.mark.asyncio
async def test_enqueue_and_read(queue):
    """Messages enqueued should be readable by read_pending."""
    stream = "test:read"
    await queue.ensure_group(stream)
    await queue.enqueue(stream, "memory_extraction", {"event_id": "evt_2", "user_id": "usr_1"})

    messages = await queue.read_pending(stream, count=10, block_ms=100)
    assert len(messages) == 1
    msg_id, data = messages[0]
    assert data["task_type"] == "memory_extraction"
    assert data["payload"]["event_id"] == "evt_2"


@pytest.mark.asyncio
async def test_ack_removes_from_pending(queue):
    """After ACK, message should not be re-read."""
    stream = "test:ack"
    await queue.ensure_group(stream)
    await queue.enqueue(stream, "proactive_planning", {"event_id": "evt_3"})

    messages = await queue.read_pending(stream, count=10, block_ms=100)
    assert len(messages) == 1
    msg_id = messages[0][0]
    await queue.ack(stream, msg_id)

    # Reading again should return no new messages
    messages2 = await queue.read_pending(stream, count=10, block_ms=100)
    assert len(messages2) == 0


@pytest.mark.asyncio
async def test_process_stream_calls_handler(queue):
    """process_stream should call handler and ACK on success."""
    stream = "test:process"
    await queue.ensure_group(stream)
    await queue.enqueue(stream, "entity_extraction", {"event_id": "evt_4"})

    handled = []

    async def handler(task_type, payload):
        handled.append((task_type, payload))

    processed = await queue.process_stream(stream, handler, count=10, block_ms=100)
    assert processed == 1
    assert len(handled) == 1
    assert handled[0][0] == "entity_extraction"
    assert handled[0][1]["event_id"] == "evt_4"


@pytest.mark.asyncio
async def test_process_stream_handles_failure(queue):
    """process_stream should not ACK on handler failure."""
    stream = "test:fail"
    await queue.ensure_group(stream)
    await queue.enqueue(stream, "bad_task", {"event_id": "evt_5"})

    async def failing_handler(task_type, payload):
        raise ValueError("handler failed")

    processed = await queue.process_stream(stream, failing_handler, count=10, block_ms=100)
    assert processed == 0  # Failed tasks aren't counted as processed
