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
from ulid import ULID

from src.errors import safe_error_event

logger = logging.getLogger(__name__)

router = APIRouter()

# Active WebSocket connections per user
_connections: dict[str, list[WebSocket]] = {}


def _backfill_message_for_surface(surface) -> dict | None:
    """Build the WS replay message for a persisted surface on reconnect.

    Surface kinds replay in different shapes:
    - ``proactive_insight`` → the live ``{"type": "surface", "surface": payload}``
      push (so insights missed while offline still render).
    - ``execution`` (and other live-update kinds) → the last ``SurfaceUpdate``
      stored under ``payload["last_surface_update"]`` as a ``surface_update``.

    Returns ``None`` when the surface has nothing to replay.
    """
    if surface.surface_type == "proactive_insight":
        if surface.payload:
            return {"type": "surface", "surface": surface.payload}
        return None
    last_update = (surface.payload or {}).get("last_surface_update")
    if last_update:
        return {"type": "surface_update", **last_update}
    return None


@router.websocket("/ws/{user_id}")
async def muldro_ws(websocket: WebSocket, user_id: str):
    """WebSocket endpoint for real-time A2UI surface updates.

    Auth via message after connect: client sends { type: "auth", token: "..." }
    as the first message. No token in URL query params.
    """
    await websocket.accept()

    # WebSocket scopes are skipped by TracingMiddleware, so they have no
    # correlation id. Mint one per connection and reuse it for every error
    # frame sent on this socket.
    cid = f"ws_{ULID()}"

    # Wait for auth message (5 second timeout)
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        message = json.loads(raw)
        if message.get("type") != "auth" or not message.get("token"):
            logger.warning("ws_auth_rejected: no auth message from %s", user_id)
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
            if not user:
                logger.warning("ws_auth_rejected: invalid/expired token for %s", user_id)
                await websocket.send_json({"type": "auth_error", "message": "Invalid token"})
                await websocket.close(code=4003, reason="Invalid token")
                return
            if user.user_id != user_id:
                logger.warning(
                    "ws_auth_rejected: token user %s != path user %s",
                    user.user_id,
                    user_id,
                )
                await websocket.send_json({"type": "auth_error", "message": "User mismatch"})
                await websocket.close(code=4003, reason="User mismatch")
                return

    except asyncio.TimeoutError:
        logger.warning("ws_auth_timeout: no auth message within 5s from %s", user_id)
        await websocket.close(code=4001, reason="Auth timeout")
        return
    except WebSocketDisconnect:
        logger.debug("ws_auth_disconnect: client disconnected during auth for %s", user_id)
        return
    except json.JSONDecodeError:
        logger.warning("ws_auth_rejected: malformed JSON from %s", user_id)
        return
    except Exception:
        logger.exception("ws_auth_error: unexpected failure for %s", user_id)
        await websocket.close(code=4003, reason="Auth validation failed")
        return

    # Auth succeeded
    await websocket.send_json({"type": "auth_ok"})

    # Backfill: send current active execution surfaces on reconnect so clients
    # recover surface state that was missed while disconnected.
    try:
        from sqlalchemy import select

        from src.api.deps import resolve_workspace_id
        from src.models.database import get_session_factory
        from src.models.ui_state import UISurface

        async with get_session_factory()() as db:
            # Scope backfill to the user's current workspace — a multi-workspace
            # user must not receive surfaces from another workspace on reconnect.
            backfill_ws_id = await resolve_workspace_id(db, user_id)
            result = await db.execute(
                select(UISurface)
                .where(
                    UISurface.user_id == user_id,
                    UISurface.workspace_id == backfill_ws_id,
                    # Replay both live execution surfaces AND proactive insights
                    # that arrived while the client was offline.
                    UISurface.surface_type.in_(("execution", "proactive_insight")),
                )
                .order_by(UISurface.updated_at.desc())
                .limit(10)
            )
            active_surfaces = result.scalars().all()

            for surface in active_surfaces:
                msg = _backfill_message_for_surface(surface)
                if msg is not None:
                    await websocket.send_text(json.dumps(msg))
    except Exception:
        logger.debug("Failed to backfill surfaces on WS connect", exc_info=True)

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
                f"muldro:a2ui:{user_id}",
                f"muldro:surface_sync:{user_id}",
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
                    await _handle_client_message(user_id, data, app, cid)
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


async def _handle_approve(user_id: str, payload: dict, app, cid: str = "") -> dict:
    """Handle approval action via the REST handler (full execution resume)."""
    approval_id = payload.get("approval_id") or payload.get("id", "")
    return await _process_approval_ws(user_id, approval_id, "approve", app, cid)


async def _handle_reject(user_id: str, payload: dict, app, cid: str = "") -> dict:
    """Handle rejection action via the REST handler (full execution resume)."""
    approval_id = payload.get("approval_id") or payload.get("id", "")
    return await _process_approval_ws(user_id, approval_id, "reject", app, cid)


async def _process_approval_ws(
    user_id: str, approval_id: str, action: str, app, cid: str = ""
) -> dict:
    """Bridge WS approval actions to the REST endpoint handlers.

    Resolves workspace_id and DB session manually (no FastAPI DI available
    in the WebSocket action path), then delegates to the same approve_action /
    reject_action functions that power the REST API.
    """
    from fastapi import HTTPException

    from src.api.deps import resolve_workspace_id
    from src.config.settings import get_settings
    from src.models.database import get_session_factory

    settings = get_settings()

    async with get_session_factory()() as db:
        try:
            workspace_id = await resolve_workspace_id(db, user_id)
        except Exception as e:
            logger.warning("ws_approval_workspace_resolve_failed: %s", e)
            return {"status": "error", "error": "Could not resolve workspace"}

        try:
            if action == "approve":
                from src.api.routes_approvals import approve_action

                result = await approve_action(
                    approval_id=approval_id,
                    req=None,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    db=db,
                    settings=settings,
                )
            else:
                from src.api.routes_approvals import reject_action

                result = await reject_action(
                    approval_id=approval_id,
                    req=None,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    db=db,
                    settings=settings,
                )

            return {
                "status": "success",
                "approval_id": result.approval_id,
                "decision": result.status,
            }
        except HTTPException as e:
            # detail is controlled, developer-authored text — safe to surface;
            # emit the standard error frame shape (code + correlation id).
            return {"status": "error", "code": "error", "message": e.detail, "correlation_id": cid}
        except Exception as e:
            logger.error("ws_approval_failed: %s", e, exc_info=True)
            return safe_error_event(e, cid, channel="ws")


async def _handle_orchestrator_action(
    user_id: str, action: str, payload: dict, app, cid: str = ""
) -> dict:
    """Generic fallback: route unhandled actions through the orchestrator."""
    orchestrator = getattr(app.state, "orchestrator", None)
    if not orchestrator:
        return {"status": "error", "error": "Orchestrator not available"}

    context = payload.get("context", "")
    surface_id = payload.get("surface_id", "")
    message = f"[Action: {action}]"
    if surface_id:
        message += f" [Surface: {surface_id}]"
    if context:
        message += f" {context}"

    try:
        from src.api.deps import resolve_workspace_id
        from src.models.database import get_session_factory

        async with get_session_factory()() as db:
            workspace_id = await resolve_workspace_id(db, user_id)

        # Interactive surface action: the user's click is authorization, so
        # execute rather than re-plan (chat-pipeline-fold drift #6 override).
        result = await orchestrator.process_message(
            user_id=user_id,
            workspace_id=workspace_id,
            message=message,
            mode="ask",
        )
        return {"status": "success", "result": result}
    except Exception as e:
        logger.warning("orchestrator_action_failed: %s", e, exc_info=True)
        return safe_error_event(e, cid, channel="ws")


async def _handle_edit_before_approve(user_id: str, payload: dict, app, cid: str = "") -> dict:
    """Handle edit-before-approve action via the REST edit endpoint."""
    return await _process_edit_approval_ws(user_id, payload, app, cid)


async def _process_edit_approval_ws(user_id: str, payload: dict, app, cid: str = "") -> dict:
    """Bridge WS edit action to the REST edit_approval endpoint.

    Resolves workspace_id and DB session manually (no FastAPI DI available
    in the WebSocket action path), then delegates to the same edit_approval
    function that powers the REST API.
    """
    from fastapi import HTTPException

    from src.api.deps import resolve_workspace_id
    from src.models.database import get_session_factory

    approval_id = payload.get("approval_id", "")

    async with get_session_factory()() as db:
        try:
            workspace_id = await resolve_workspace_id(db, user_id)
        except Exception as e:
            logger.warning("ws_edit_approval_workspace_resolve_failed: %s", e)
            return {"status": "error", "error": "Could not resolve workspace"}

        try:
            from src.api.routes_approvals import ApprovalEditRequest, edit_approval

            req = ApprovalEditRequest(
                title=payload.get("title"),
                summary=payload.get("summary"),
                risk_level=payload.get("risk_level"),
            )
            result = await edit_approval(
                approval_id=approval_id,
                req=req,
                user_id=user_id,
                workspace_id=workspace_id,
                db=db,
            )
            return {
                "status": "success",
                "approval_id": result.approval_id,
                "title": result.title,
                "summary": result.summary,
            }
        except HTTPException as e:
            # detail is controlled, developer-authored text — safe to surface;
            # emit the standard error frame shape (code + correlation id).
            return {"status": "error", "code": "error", "message": e.detail, "correlation_id": cid}
        except Exception as e:
            logger.error("ws_edit_approval_failed: %s", e, exc_info=True)
            return safe_error_event(e, cid, channel="ws")


async def _handle_execute_insight(user_id: str, payload: dict, app, cid: str = "") -> dict:
    """Handle insight action execution — transitions insight to execution surface.

    When a user clicks a suggested action on an insight surface, this handler:
    1. Fetches the insight surface and the selected action
    2. Records engagement
    3. Executes via the orchestrator
    """
    from src.api.deps import resolve_workspace_id
    from src.models.database import get_session_factory

    surface_id = payload.get("surface_id", "")
    action_index = payload.get("action_index", 0)

    if not surface_id:
        return {"status": "error", "error": "surface_id required"}

    async with get_session_factory()() as db:
        try:
            workspace_id = await resolve_workspace_id(db, user_id)
        except Exception as e:
            logger.warning("ws_insight_workspace_resolve_failed: %s", e)
            return {"status": "error", "error": "Could not resolve workspace"}

        # Fetch the insight surface
        from sqlalchemy import select

        from src.models.ui_state import UISurface

        result = await db.execute(
            select(UISurface).where(
                UISurface.surface_id == surface_id,
                UISurface.user_id == user_id,
                UISurface.workspace_id == workspace_id,
                UISurface.surface_type == "proactive_insight",
            )
        )
        surface = result.scalar_one_or_none()
        if not surface:
            return {"status": "error", "error": "Insight surface not found"}

        payload_data = surface.payload or {}
        insight_data = payload_data.get("insight_data", {})
        actions = insight_data.get("suggested_actions", [])

        if action_index >= len(actions):
            return {"status": "error", "error": "Invalid action index"}

        selected = actions[action_index]

        # Record engagement
        from src.services.engagement_service import EngagementService

        eng_svc = EngagementService(db, workspace_id)
        await eng_svc.record_engagement(
            insight_data.get("signal_source", "unknown"),
            insight_data.get("signal_category", "unknown"),
            "engaged",
        )
        await db.commit()

    # Execute via orchestrator
    orchestrator = getattr(app.state, "orchestrator", None)
    if not orchestrator:
        return {"status": "error", "error": "Orchestrator not available"}

    capability = str(selected.get("capability") or "").strip()
    if not capability:
        # No capability means nothing for the trust gate to evaluate. Refuse
        # rather than fall back to re-planning from the description, which is
        # how the ungated path existed in the first place.
        logger.warning(
            "ws_execute_insight_no_capability: surface=%s index=%s", surface_id, action_index
        )
        return {"status": "error", "error": "Suggested action has no capability"}

    action_input = selected.get("action_input")
    if not isinstance(action_input, dict):
        action_input = {}

    try:
        run_id = await _queue_insight_action(
            user_id=user_id,
            workspace_id=workspace_id,
            surface_id=surface_id,
            capability=capability,
            action_input=action_input,
        )
        if not run_id:
            return {"status": "error", "error": "Could not queue insight action"}
        return {
            "status": "success",
            "surface_id": surface_id,
            "capability": capability,
            "run_id": run_id,
        }
    except Exception as e:
        logger.warning("ws_execute_insight_failed: %s", e, exc_info=True)
        return safe_error_event(e, cid, channel="ws")


async def _queue_insight_action(
    *,
    user_id: str,
    workspace_id: str,
    surface_id: str,
    capability: str,
    action_input: dict,
) -> str | None:
    """Queue one structured insight action as a GATED autonomous run.

    Why this exists rather than a ``process_message`` call:
    ``RelevanceAssessment.suggested_actions[].description`` is prose written by a
    model that was reading attacker-controllable content (an email body, a Slack
    message). Passing it to ``process_message`` relabels it as the founder's own
    words -- that path carries ``authorization_source=DIRECT_USER_REQUEST``, so
    ``trust_gate`` returns early ("the user's message IS the authorization") and
    ``permission_gate`` is not installed at all (it requires ``deep_single_lead``,
    which is off). One click then produced an ungated external write.

    Note what this does and does not fix. ``capability`` and ``action_input`` are
    *also* model-authored, so they are not trusted here -- they are **gated**.
    Routing through a persisted Plan and a ``source="background"`` TaskRun puts the
    action behind ``DagRunner``'s TrustEngine evaluation, the same gate every other
    autonomous write passes. The founder sees the real capability and arguments in
    the approval, instead of a re-plan derived from prose.

    The description is deliberately not carried into the plan. The founder already
    read it on the card; the runtime has no use for it and every reason not to.

    Returns the run id, or ``None`` if the plan was not persisted (idempotent skip).
    """
    from src.config.settings import get_settings
    from src.contracts import PlanOutput, PlanStep
    from src.models.database import get_session_factory
    from src.orchestrator.plan_store import PlanStore
    from src.services.graph_executor_factory import create_graph_executor

    settings = get_settings()
    factory = get_session_factory()

    plan = PlanOutput(
        goal=f"Insight action: {capability}",
        reasoning=f"Founder selected a suggested action on insight surface {surface_id}.",
        steps=[
            PlanStep(
                step_id="s1",
                description=f"Execute {capability} (insight {surface_id})",
                actor="muldro",
                capability=capability,
                input=action_input,
            )
        ],
    )

    store = PlanStore(lambda: factory)
    persisted = await store.persist_plan_record(
        plan,
        user_id,
        workspace_id,
        trigger_type="insight_action",
        idempotency_key=f"insight:{surface_id}:{capability}",
    )
    if not persisted.plan_id:
        logger.info("insight_action_plan_not_persisted: surface=%s", surface_id)
        return None

    async with factory() as db:
        executor = await create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)
        run = await executor.create_run(
            plan_id=persisted.plan_id,
            user_id=user_id,
            workspace_id=workspace_id,
            source="background",
        )
        await db.commit()
        logger.info(
            "insight_action_queued: surface=%s capability=%s run=%s",
            surface_id,
            capability,
            run.run_id,
        )
        return run.run_id


# Registry of named action handlers
ACTION_HANDLERS: dict[str, object] = {
    "approve": _handle_approve,
    "reject": _handle_reject,
    "edit_before_approve": _handle_edit_before_approve,
    "execute_insight": _handle_execute_insight,
}


async def _dispatch_action(user_id: str, action: str, payload: dict, app, cid: str = "") -> dict:
    """Dispatch an action to the appropriate handler, always returning a result."""
    handler = ACTION_HANDLERS.get(action)
    if handler:
        return await handler(user_id, payload, app, cid)
    return await _handle_orchestrator_action(user_id, action, payload, app, cid)


async def _handle_client_message(user_id: str, raw: str, app, cid: str = "") -> None:
    """Handle incoming WebSocket messages (A2UI actions).

    Every action gets an action_result response — the frontend always
    knows whether the action succeeded or failed.
    """
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ws_invalid_json", extra={"user_id": user_id})
        return

    msg_type = message.get("type")

    if msg_type == "action":
        payload = message.get("payload", {})
        action = payload.get("action", "")

        if not action:
            await _broadcast(
                user_id,
                {
                    "type": "action_result",
                    "action": "",
                    "status": "error",
                    "error": "No action specified",
                },
            )
            return

        try:
            result = await _dispatch_action(user_id, action, payload, app, cid)
            await _broadcast(
                user_id,
                {
                    "type": "action_result",
                    "action": action,
                    "status": result.get("status", "success"),
                    "result": result,
                },
            )
        except Exception as e:
            logger.warning("action_dispatch_error: %s", e, exc_info=True)
            safe = safe_error_event(e, cid, channel="ws")
            await _broadcast(
                user_id,
                {
                    "type": "action_result",
                    "action": action,
                    "status": "error",
                    "error": safe["message"],
                    "code": safe["code"],
                    "correlation_id": safe["correlation_id"],
                },
            )

    elif msg_type == "heartbeat":
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
