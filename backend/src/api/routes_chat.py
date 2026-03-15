"""SSE streaming chat endpoint — routes messages through the full orchestrator.

This is the primary chat entry point. Unlike /v1/jarvis/command (which only
calls the Planner), this endpoint streams through the full multi-agent pipeline:
Planner → Governor → Presenter → Persona, with real-time visibility into
agent routing, tool calls, and thinking.
"""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.deps import get_current_user_id
from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    surface: str = "web"
    context: dict | None = None
    conversation_id: str | None = None


def _build_orchestrator(settings: Settings):
    """Build orchestrator with all services for the API process."""
    from src.models.database import get_session_factory
    from src.orchestrator.jarvis import JarvisOrchestrator
    from src.tools import intelligence_server

    db_factory = get_session_factory()

    services = {}
    try:
        from src.services.event_processor import EventProcessor

        services["event_processor"] = EventProcessor(settings)
    except Exception:
        pass
    try:
        from src.services.planner import Planner

        services["planner"] = Planner(settings)
    except Exception:
        pass
    try:
        from src.services.governor import Governor

        services["governor"] = Governor(settings)
    except Exception:
        pass
    try:
        from src.services.presenter import Presenter

        services["presenter"] = Presenter(settings)
    except Exception:
        pass
    try:
        from src.services.world_model import WorldModel

        db = db_factory()
        services["world_model"] = WorldModel(settings, db)
    except Exception:
        pass
    try:
        from src.services.memory_service import MemoryService

        db = db_factory()
        services["memory_service"] = MemoryService(settings, db)
        services["memory"] = services["memory_service"]
    except Exception:
        pass
    try:
        from src.services.audit import AuditService

        services["audit"] = AuditService()
    except Exception:
        pass
    try:
        from src.services.vector_store import VectorStore

        services["vector_store"] = VectorStore(settings)
    except Exception:
        pass
    try:
        from src.services.search_service import SearchService

        services["search_service"] = SearchService(
            settings, vector_store=services.get("vector_store")
        )
    except Exception:
        pass
    try:
        from src.services.working_memory import WorkingMemoryService

        db = db_factory()
        services["working_memory"] = WorkingMemoryService(settings, db)
    except Exception:
        pass

    intelligence_server.configure(db_factory, settings, services)

    return JarvisOrchestrator(
        settings=settings,
        db_factory=db_factory,
        services=services,
    )


# Lazy singleton — created on first request
_orchestrator = None


def _get_orchestrator(settings: Settings):
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = _build_orchestrator(settings)
    return _orchestrator


@router.post("/v1/jarvis/chat")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """Stream a chat response through the full multi-agent orchestrator.

    Returns Server-Sent Events with the following event types:
      - trace: {trace_id}
      - agent_start: {agent, model}
      - thinking: {agent, text}
      - tool_call: {agent, tool, input}
      - tool_result: {agent, tool, result, blocked?, latency_ms?}
      - decision: {decision}
      - agent_done: {agent, text, input_tokens, output_tokens, latency_ms}
      - response: {text}  — the final user-facing response
      - error: {message}
      - done: {trace_id}
    """
    orchestrator = _get_orchestrator(settings)

    # Resolve or create conversation
    conversation_id = req.conversation_id
    if not conversation_id:
        try:
            from ulid import ULID

            from src.models.conversations import Conversation
            from src.models.database import get_session_factory

            async with get_session_factory()() as db:
                convo = Conversation(
                    conversation_id=f"conv_{ULID()}",
                    user_id=user_id,
                    surface=req.surface,
                    status="active",
                )
                db.add(convo)
                await db.commit()
                conversation_id = convo.conversation_id
        except Exception:
            logger.warning("Failed to create conversation record", exc_info=True)
            conversation_id = None

    # Save user message
    if conversation_id:
        try:
            from ulid import ULID

            from src.models.conversations import Message
            from src.models.database import get_session_factory

            async with get_session_factory()() as db:
                db.add(
                    Message(
                        message_id=f"msg_{ULID()}",
                        conversation_id=conversation_id,
                        role="user",
                        content=req.message,
                        surface=req.surface,
                    )
                )
                await db.commit()
        except Exception:
            logger.warning("Failed to save user message", exc_info=True)

    final_response_text = ""
    final_trace_id = None

    async def event_generator():
        nonlocal final_response_text, final_trace_id
        try:
            # Send conversation_id as first event
            if conversation_id:
                cid_data = json.dumps(
                    {"event": "conversation", "conversation_id": conversation_id}
                )
                yield f"event: conversation\ndata: {cid_data}\n\n"

            async for event in orchestrator.process_message_stream(
                message=req.message,
                surface=req.surface,
                context=req.context,
            ):
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                event_type = event.get("event", "message")

                if event_type == "response":
                    final_response_text = event.get("text", "")
                if event_type == "trace":
                    final_trace_id = event.get("trace_id")

                data = json.dumps(event, default=str)
                yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception as e:
            logger.error("Chat stream error: %s", e, exc_info=True)
            error_data = json.dumps({"event": "error", "message": str(e)})
            yield f"event: error\ndata: {error_data}\n\n"
        finally:
            # Save assistant response
            if conversation_id and final_response_text:
                try:
                    from datetime import datetime, timezone

                    from ulid import ULID

                    from src.models.conversations import Conversation, Message
                    from src.models.database import get_session_factory

                    async with get_session_factory()() as db:
                        db.add(
                            Message(
                                message_id=f"msg_{ULID()}",
                                conversation_id=conversation_id,
                                role="assistant",
                                content=final_response_text,
                                metadata_={"trace_id": final_trace_id},
                                surface=req.surface,
                            )
                        )
                        # Update last_active_at
                        from sqlalchemy import update

                        await db.execute(
                            update(Conversation)
                            .where(
                                Conversation.conversation_id == conversation_id
                            )
                            .values(last_active_at=datetime.now(timezone.utc))
                        )
                        await db.commit()
                except Exception:
                    logger.warning("Failed to save assistant message", exc_info=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
