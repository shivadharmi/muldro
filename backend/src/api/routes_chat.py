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

from src.api.deps import get_current_user_id, get_current_workspace_id
from src.config.settings import Settings, get_settings
from src.orchestrator.contracts import (
    MessageAgentStep,
    MessageMetadata,
    MessageToolCall,
    PlannerOutput,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    surface: str = "web"
    mode: str = "ask"  # ask, plan, execute
    context: dict | None = None
    conversation_id: str | None = None


def _build_orchestrator(settings: Settings, gateway=None):
    """Build orchestrator with all services for the API process."""
    from src.models.database import get_session_factory
    from src.orchestrator.jarvis import JarvisOrchestrator
    from src.runtime import build as build_runtime
    from src.tools import intelligence_server

    db_factory = get_session_factory()

    # Long-lived session for services that hold a db reference.
    # These persist for the orchestrator lifetime (one per API process).
    # Stored as module-level so it can be closed during app shutdown.
    svc_db = db_factory()
    _module_svc_db_ref.append(svc_db)

    svc = build_runtime(settings, svc_db)
    intelligence_server.configure(db_factory, settings, svc)

    return JarvisOrchestrator(
        settings=settings,
        db_factory=db_factory,
        services=svc,
        gateway=gateway,
    )


# Lazy singleton — created on first request
_orchestrator = None
_module_svc_db_ref: list = []  # holds the long-lived session for shutdown cleanup


async def _get_orchestrator(settings: Settings, gateway=None):
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = _build_orchestrator(settings, gateway=gateway)
        await _orchestrator.load_agents_from_db()
    return _orchestrator


@router.post("/v1/jarvis/chat")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
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
    gateway = getattr(request.app.state, "mcp_gateway", None)
    orchestrator = await _get_orchestrator(settings, gateway=gateway)

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
                    workspace_id=workspace_id,
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
                        workspace_id=workspace_id,
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
    final_decision: PlannerOutput | None = None
    agent_steps: list[MessageAgentStep] = []

    async def event_generator():
        nonlocal final_response_text, final_trace_id, final_decision
        try:
            # Send conversation_id as first event
            if conversation_id:
                cid_data = json.dumps({"event": "conversation", "conversation_id": conversation_id})
                yield f"event: conversation\ndata: {cid_data}\n\n"

            async for event in orchestrator.process_message_stream(
                message=req.message,
                user_id=user_id,
                workspace_id=workspace_id,
                surface=req.surface,
                mode=req.mode,
                context=req.context,
                conversation_id=conversation_id,
            ):
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                event_type = event.get("event", "message")

                if event_type == "response":
                    final_response_text = event.get("text", "")
                if event_type == "trace":
                    final_trace_id = event.get("trace_id")
                if event_type == "decision":
                    raw = event.get("decision")
                    if isinstance(raw, dict):
                        final_decision = PlannerOutput.model_validate(raw)

                # Collect agent step data using Pydantic models
                if event_type == "agent_start":
                    agent_steps.append(
                        MessageAgentStep(
                            agent=event.get("agent", "unknown"),
                            model=event.get("model"),
                        )
                    )
                elif event_type == "agent_done" and agent_steps:
                    agent_name = event.get("agent")
                    for step in agent_steps:
                        if step.agent == agent_name:
                            step.response_text = event.get("text", "")
                            step.input_tokens = event.get("input_tokens")
                            step.output_tokens = event.get("output_tokens")
                            step.cache_creation_tokens = event.get("cache_creation_tokens")
                            step.cache_read_tokens = event.get("cache_read_tokens")
                            step.cost_usd = event.get("cost_usd")
                            step.latency_ms = event.get("latency_ms")
                            step.status = "done"
                            break
                elif event_type == "thinking" and agent_steps:
                    agent_name = event.get("agent")
                    is_thinking = event.get("is_thinking", False)
                    text = event.get("text", "")
                    for step in reversed(agent_steps):
                        if step.agent == agent_name:
                            if is_thinking:
                                # Extended thinking — accumulate preview
                                if not step.thinking_preview:
                                    step.thinking_preview = ""
                                if len(step.thinking_preview) < 2000:
                                    step.thinking_preview += text
                                    if len(step.thinking_preview) > 2000:
                                        step.thinking_preview = step.thinking_preview[:2000]
                            else:
                                # Agent reasoning text — accumulate
                                if not step.reasoning_text:
                                    step.reasoning_text = ""
                                if len(step.reasoning_text) < 2000:
                                    step.reasoning_text += text
                                    if len(step.reasoning_text) > 2000:
                                        step.reasoning_text = step.reasoning_text[:2000]
                            break
                elif event_type == "tool_call" and agent_steps:
                    agent_name = event.get("agent")
                    for step in reversed(agent_steps):
                        if step.agent == agent_name:
                            step.tool_calls.append(
                                MessageToolCall(
                                    tool_name=event.get("tool", ""),
                                    tool_input=event.get("input", {}),
                                    status="success",
                                )
                            )
                            break
                elif event_type == "tool_result" and agent_steps:
                    agent_name = event.get("agent")
                    for step in reversed(agent_steps):
                        if step.agent == agent_name and step.tool_calls:
                            tc = step.tool_calls[-1]
                            if event.get("blocked", False):
                                tc.status = "blocked"
                            tc.duration_ms = event.get("latency_ms", 0)
                            # Store result preview (truncated)
                            raw_result = event.get("result")
                            if raw_result is not None:
                                preview = json.dumps(raw_result, default=str)
                                tc.result_preview = preview[:500] if len(preview) > 500 else preview
                            break

                data = json.dumps(event, default=str)
                yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception as e:
            logger.error("Chat stream error: %s", e, exc_info=True)
            error_data = json.dumps({"event": "error", "message": str(e)})
            yield f"event: error\ndata: {error_data}\n\n"
        finally:
            # Save assistant response with typed metadata
            if conversation_id and final_response_text:
                try:
                    from datetime import datetime, timezone

                    from ulid import ULID

                    from src.models.conversations import Conversation, Message
                    from src.models.database import get_session_factory

                    metadata = MessageMetadata(
                        trace_id=final_trace_id,
                        decision=final_decision,
                        agent_steps=agent_steps if agent_steps else [],
                    )

                    # Compute aggregate token/cost from agent steps
                    total_input = sum(s.input_tokens or 0 for s in agent_steps)
                    total_output = sum(s.output_tokens or 0 for s in agent_steps)
                    total_cost = sum(s.cost_usd or 0.0 for s in agent_steps)

                    async with get_session_factory()() as db:
                        db.add(
                            Message(
                                message_id=f"msg_{ULID()}",
                                conversation_id=conversation_id,
                                workspace_id=workspace_id,
                                role="assistant",
                                content=final_response_text,
                                metadata_=metadata.model_dump(mode="json"),
                                surface=req.surface,
                                trace_id=final_trace_id,
                                input_tokens=total_input if total_input else None,
                                output_tokens=total_output if total_output else None,
                                cost_usd=total_cost if total_cost else None,
                            )
                        )
                        # Update conversation aggregates
                        from sqlalchemy import update

                        await db.execute(
                            update(Conversation)
                            .where(Conversation.conversation_id == conversation_id)
                            .values(
                                last_active_at=datetime.now(timezone.utc),
                                message_count=Conversation.message_count + 2,  # user + assistant
                                total_input_tokens=Conversation.total_input_tokens + total_input,
                                total_output_tokens=Conversation.total_output_tokens + total_output,
                                total_cost_usd=Conversation.total_cost_usd + total_cost,
                            )
                        )
                        await db.commit()
                except Exception:
                    logger.warning("Failed to save assistant message", exc_info=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
