"""Realtime SSE routes — server-sent events for live streaming."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api.deps import get_current_user_id

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/v1/realtime/events")
async def stream_global_events(
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    """SSE endpoint for global event streaming.

    Subscribes to the user's Redis pub/sub channel and streams events
    to the client as server-sent events.
    """
    redis = getattr(request.app.state, "redis", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable for realtime streaming")

    channel_name = f"jarvis:realtime:{user_id}"

    async def event_generator():
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_name)
        try:
            while True:
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    event_type = "message"
                    try:
                        parsed = json.loads(data)
                        event_type = parsed.get("event_type", "message")
                    except (json.JSONDecodeError, TypeError):
                        pass
                    yield f"event: {event_type}\ndata: {data}\n\n"
                else:
                    # Keepalive comment to detect disconnects
                    yield ": keepalive\n\n"
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/v1/realtime/runs/{run_id}")
async def stream_run_progress(
    run_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    """SSE endpoint for run-specific progress streaming.

    Subscribes to a run-specific Redis pub/sub channel and streams
    execution progress events to the client.
    """
    redis = getattr(request.app.state, "redis", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable for realtime streaming")

    channel_name = f"jarvis:run:{run_id}"

    async def event_generator():
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_name)
        try:
            while True:
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    event_type = "progress"
                    try:
                        parsed = json.loads(data)
                        event_type = parsed.get("event_type", "progress")
                        if parsed.get("status") in ("completed", "failed", "cancelled"):
                            yield f"event: {event_type}\ndata: {data}\n\n"
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass
                    yield f"event: {event_type}\ndata: {data}\n\n"
                else:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
