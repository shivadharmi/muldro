"""Realtime SSE routes — server-sent events for live streaming."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.models.database import get_db
from src.models.task_graph import TaskRun

router = APIRouter()
logger = logging.getLogger(__name__)


async def _resolve_user_id_for_sse(
    authorization: str | None = Header(None),
    token: str | None = Query(None, description="Auth token for EventSource (no header support)"),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Resolve user_id from Bearer header OR query param token.

    The browser-native EventSource API cannot send custom headers,
    so SSE endpoints accept the session token as a query parameter
    as a fallback.
    """
    raw_token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.removeprefix("Bearer ")
    elif token:
        raw_token = token

    if not raw_token:
        raise HTTPException(status_code=401, detail="Missing authorization")

    from src.services.auth_service import AuthService

    auth = AuthService(settings, db)
    user = await auth.validate_session(raw_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user.user_id


@router.get("/v1/realtime/events")
async def stream_global_events(
    request: Request,
    user_id: str = Depends(_resolve_user_id_for_sse),
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


@router.get("/v1/realtime/runtime")
async def stream_runtime_events(
    request: Request,
    user_id: str = Depends(_resolve_user_id_for_sse),
):
    """SSE endpoint for runtime lifecycle events (command, route, run, step, tool).

    Subscribes to the user's agent event stream and filters for runtime events.
    The frontend activity store can subscribe to this for live updates.
    """
    redis = getattr(request.app.state, "redis", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable for realtime streaming")

    channel_name = f"jarvis:agent_events:{user_id}"

    runtime_event_types = {
        "command_received",
        "route_selected",
        "plan_created",
        "run_created",
        "step_started",
        "step_completed",
        "step_failed",
        "step_blocked",
        "approval_requested",
        "approval_resolved",
        "tool_call_started",
        "tool_call_completed",
        "tool_call_failed",
        "fallback_selected",
        "artifact_created",
        "surface_created",
        "agent_started",
        "agent_completed",
        "run_completed",
        "run_failed",
        "run_cancelled",
    }

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
                    try:
                        parsed = json.loads(data)
                        event_type = parsed.get("event_type", "")
                        if event_type in runtime_event_types:
                            yield f"event: {event_type}\ndata: {data}\n\n"
                    except (json.JSONDecodeError, TypeError):
                        pass
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


@router.get("/v1/realtime/runs/{run_id}")
async def stream_run_progress(
    run_id: str,
    request: Request,
    user_id: str = Depends(_resolve_user_id_for_sse),
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint for run-specific progress streaming.

    Subscribes to a run-specific Redis pub/sub channel and streams
    execution progress events to the client.
    """
    # Authorization gate (runs before any resource access): the requester may
    # only stream progress for runs they own. Without this check, any
    # authenticated user could subscribe to `jarvis:run:{run_id}` for an
    # arbitrary run_id and observe another user's execution events (IDOR).
    # A 404 (not 403) is returned so the response does not reveal whether the
    # run exists — matching `get_history_detail`'s ownership convention.
    owned = await db.execute(
        select(TaskRun.run_id).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
        )
    )
    if owned.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Run not found")

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
