"""WebSocket endpoint for streaming A2UI surfaces and notifications.

Clients connect to /ws/{user_id} and authenticate via an auth message:
  { "type": "auth", "token": "<session_token>" }

After authentication, clients receive real-time updates:
- A2UI surface payloads (briefings, approvals, dashboards)
- Notification events
- Surface sync events (action taken on another surface)
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

# Active WebSocket connections per user
_connections: dict[str, list[WebSocket]] = {}


@router.websocket("/ws/{user_id}")
async def jarvis_ws(websocket: WebSocket, user_id: str):
    """WebSocket endpoint for real-time A2UI surface updates.

    Auth via message after connect: client sends { type: "auth", token: "..." }
    as the first message. No token in URL query params.
    """
    await websocket.accept()

    # Wait for auth message (5 second timeout)
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        message = json.loads(raw)
        if message.get("type") != "auth" or not message.get("token"):
            await websocket.send_json({"type": "auth_error", "message": "Expected auth message"})
            await websocket.close(code=4001, reason="Auth required")
            return

        token = message["token"]
        from src.config.settings import get_settings
        from src.models.database import get_session_factory
        from src.services.auth_service import AuthService

        settings = get_settings()
        async with get_session_factory()() as db:
            auth = AuthService(settings, db)
            user = await auth.validate_session(token)
            if not user or user.user_id != user_id:
                await websocket.send_json({"type": "auth_error", "message": "Invalid token"})
                await websocket.close(code=4003, reason="Invalid token")
                return

    except asyncio.TimeoutError:
        await websocket.close(code=4001, reason="Auth timeout")
        return
    except (json.JSONDecodeError, WebSocketDisconnect):
        return
    except Exception:
        await websocket.close(code=4003, reason="Auth validation failed")
        return

    # Auth succeeded
    await websocket.send_json({"type": "auth_ok"})

    # Track connection
    _connections.setdefault(user_id, []).append(websocket)
    logger.info("ws_connected", extra={"user_id": user_id})

    # Register web surface
    app = websocket.app
    registry = getattr(app.state, "surface_registry", None)
    if registry:
        await registry.register(user_id, "web")

    redis = getattr(app.state, "redis", None)
    pubsub = None

    try:
        tasks = []

        if redis:
            # Subscribe to user's A2UI channel and surface sync channel
            pubsub = redis.pubsub()
            await pubsub.subscribe(
                f"jarvis:a2ui:{user_id}",
                f"jarvis:surface_sync:{user_id}",
            )

            # Task: forward Redis pub/sub messages to WebSocket
            async def relay_pubsub():
                try:
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            await websocket.send_text(message["data"])
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error("pubsub_relay_error: %s", e)

            tasks.append(asyncio.create_task(relay_pubsub()))

        # Task: handle incoming messages from client
        async def handle_client():
            try:
                while True:
                    data = await websocket.receive_text()
                    await _handle_client_message(user_id, data, app)
            except WebSocketDisconnect:
                pass
            except asyncio.CancelledError:
                pass

        tasks.append(asyncio.create_task(handle_client()))

        # Task: periodic heartbeat
        async def heartbeat():
            try:
                while True:
                    await asyncio.sleep(30)
                    await websocket.send_json({"type": "heartbeat"})
                    if registry:
                        await registry.heartbeat(user_id, "web")
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        tasks.append(asyncio.create_task(heartbeat()))

        # Wait for any task to complete (client disconnect or error)
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("ws_error", extra={"user_id": user_id, "error": str(e)})
    finally:
        # Cleanup
        conns = _connections.get(user_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not _connections.get(user_id):
            _connections.pop(user_id, None)

        if registry:
            await registry.unregister(user_id, "web")

        if pubsub:
            await pubsub.unsubscribe()
            await pubsub.aclose()

        logger.info("ws_disconnected", extra={"user_id": user_id})


async def _handle_client_message(user_id: str, raw: str, app) -> None:
    """Handle incoming WebSocket messages (A2UI actions)."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ws_invalid_json", extra={"user_id": user_id})
        return

    msg_type = message.get("type")

    if msg_type == "action":
        # A2UI action from a button click or form submission
        payload = message.get("payload", {})
        action = payload.get("action")

        if action == "approve" and "id" in payload:
            from src.tools.intelligence_server import approve_action

            result = await approve_action(
                approval_id=payload["id"],
                decision="approved",
                reason="Approved via web dashboard",
            )
            await _broadcast(
                user_id,
                {
                    "type": "action_result",
                    "action": "approve",
                    "result": result,
                },
            )

        elif action == "reject" and "id" in payload:
            from src.tools.intelligence_server import approve_action

            result = await approve_action(
                approval_id=payload["id"],
                decision="rejected",
                reason="Rejected via web dashboard",
            )
            await _broadcast(
                user_id,
                {
                    "type": "action_result",
                    "action": "reject",
                    "result": result,
                },
            )

        elif action == "meeting_prep" and "event_id" in payload:
            # Trigger meeting prep generation
            logger.info(
                "ws_meeting_prep_requested",
                extra={"user_id": user_id, "event_id": payload["event_id"]},
            )

    elif msg_type == "heartbeat":
        # Client heartbeat — just acknowledge
        pass


async def _broadcast(user_id: str, message: dict) -> None:
    """Broadcast a message to all WebSocket connections for a user."""
    connections = _connections.get(user_id, [])
    text = json.dumps(message, default=str)
    for ws in connections:
        try:
            await ws.send_text(text)
        except Exception:
            pass


async def broadcast_to_user(user_id: str, message: dict) -> int:
    """Public API: broadcast a message to all WebSocket connections.

    Returns the number of connections that received the message.
    """
    connections = _connections.get(user_id, [])
    if not connections:
        return 0

    text = json.dumps(message, default=str)
    sent = 0
    for ws in connections:
        try:
            await ws.send_text(text)
            sent += 1
        except Exception:
            pass
    return sent


def get_connected_users() -> list[str]:
    """Return list of user IDs with active WebSocket connections."""
    return list(_connections.keys())
