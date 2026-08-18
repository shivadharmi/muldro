"""SSE streaming chat endpoint — routes messages through the full orchestrator.

This is the primary chat entry point. Unlike /v1/muldro/command (which only
calls the Planner), this endpoint streams through the full multi-agent pipeline:
Planner → Governor → Presenter → Persona, with real-time visibility into
agent routing, tool calls, and thinking.
"""

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.deps import get_current_user_id, get_current_workspace_id
from src.config.settings import Settings, get_settings
from src.contracts import (
    MessageAgentStep,
    MessageMetadata,
    MessageToolCall,
    PlanOutput,
)
from src.errors import safe_error_event
from src.middleware.observability import get_correlation_id
from src.orchestrator.core_events import (
    AgentDone,
    AgentStarted,
    AgentThinking,
    AgentToolCall,
    AgentToolResult,
    PlanReady,
    Presentation,
    TraceStarted,
    core_event_to_sse,
)
from src.services.workspace_entitlements import workspace_default_permission_mode

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    surface: str = "web"
    # P3b (chat permission model): the legacy ``mode`` (ask/plan/execute) is retired from the
    # HTTP contract — the user-facing control is now ``permission_mode``. The interactive handler
    # forwards a fixed ``mode="ask"`` to keep the live legacy per-step path byte-identical for
    # non-pinned callers; the internal ``mode`` param survives only for the pinned callers
    # (schedule_dispatch ``execute`` / routes_ws ``ask``), which invoke the orchestrator directly.
    # Action-time permission mode, INDEPENDENT of the retired ``mode``; only ``"bypass"`` activates
    # the deep single-lead path. Constrained to the taxonomy so a typo 422s loudly.
    # P3c: optional so the interactive handler can substitute the per-workspace default when the
    # client omits it (``None``). An explicit value always wins. Resolved at the handler, never in
    # ``_process_core`` (which pinned callers share), so a workspace ``bypass`` default cannot leak
    # onto scheduled/autonomous turns.
    permission_mode: Literal["auto", "ask", "bypass"] | None = None
    context: dict | None = None
    conversation_id: str | None = None


async def _resolve_request_permission_mode(requested, db_factory, workspace_id: str) -> str:
    """Resolve the effective per-turn permission_mode for an INTERACTIVE chat request.

    An explicit value wins; when omitted (``None``), substitute the per-workspace default
    (fail-safe ``auto``). Called ONLY from the interactive handler — the pinned callers
    (schedule_dispatch, routes_ws) invoke ``process_message*`` directly and never reach here, so a
    workspace ``bypass`` default can never leak onto scheduled/autonomous turns.
    """
    if requested is not None:
        return requested
    return await workspace_default_permission_mode(db_factory, workspace_id)


def _build_orchestrator(settings: Settings, checkpointer_provider=None):
    """Build the process-wide orchestrator with session-free shared services.

    The orchestrator holds only the shared singletons (``build_shared``); every
    DB-bound service is built per request against a fresh ``AsyncSession`` via
    ``MuldroOrchestrator._request_services``. This avoids sharing one
    long-lived session across concurrent chat requests (P2 #4).

    ``checkpointer_provider`` is a zero-arg callable that returns the durable
    LangGraph checkpointer from ``app.state.deep_checkpointer`` (Step 6A.5).
    None (default) falls back to MemorySaver inside AgentInvoker.
    """
    from src.models.database import get_session_factory
    from src.orchestrator.muldro import MuldroOrchestrator
    from src.runtime import build_shared
    from src.tools import configure_tool_servers

    db_factory = get_session_factory()
    svc = build_shared(settings)
    # Pass None: internal tools resolve the thread-local (per-loop) session factory,
    # never a shared global bound to another thread's loop (Step 11 Phase 3).
    configure_tool_servers(None, settings, svc)

    # Track the shared Redis client for shutdown cleanup (build_shared opens one
    # process-wide client reused by the orchestrator + per-request services).
    shared_redis = svc.extras.get("redis")
    if shared_redis is not None:
        _module_shared_redis.append(shared_redis)

    return MuldroOrchestrator(
        settings=settings,
        db_factory=db_factory,
        services=svc,
        checkpointer_provider=checkpointer_provider,
    )


# Lazy singleton — created on first request
_orchestrator = None
_module_shared_redis: list = []  # shared Redis client(s) to close at shutdown


async def _get_orchestrator(settings: Settings, app=None):
    """Return the process-wide orchestrator, building it on first call.

    ``app`` — the FastAPI application instance; when provided, the deep-runtime
    checkpointer is read from ``app.state.deep_checkpointer`` so the durable
    saver (opened at lifespan) is threaded into the invoker without a module
    global. Omitting ``app`` (e.g. non-chat call sites) falls back to None.
    """
    global _orchestrator
    if _orchestrator is None:
        provider = (
            (lambda: getattr(app.state, "deep_checkpointer", None)) if app is not None else None
        )
        _orchestrator = _build_orchestrator(settings, checkpointer_provider=provider)
        await _orchestrator.load_agents_from_db()
    return _orchestrator


async def shutdown_orchestrator() -> None:
    """Release process-wide orchestrator resources at app shutdown.

    Awaits the orchestrator's background tasks and closes the shared Redis
    client opened by ``build_shared`` (P2 #4 polish — previously leaked).
    """
    global _orchestrator
    if _orchestrator is not None:
        try:
            await _orchestrator.shutdown()
        except Exception:
            logger.debug("orchestrator.shutdown failed", exc_info=True)
        _orchestrator = None
    for redis in _module_shared_redis:
        try:
            await redis.aclose()
        except Exception:
            logger.debug("shared Redis aclose failed", exc_info=True)
    _module_shared_redis.clear()


async def _stream_and_persist_chat(
    events,
    *,
    request: Request,
    user_id: str,
    conversation_id: str | None,
    workspace_id: str,
    surface: str,
    assistant_message_id: str,
    count_user_message: bool = True,
):
    """Fold a typed ``CoreEvent`` stream into the persisted-message metadata, serialize each
    to its SSE frame, and persist the assistant reply + conversation aggregates in the
    ``finally``. Shared by the initial chat turn (:func:`chat_stream`) and the approval-resume
    continuation (:func:`chat_resume`) — both fold the SAME typed stream (``process_message_
    events`` / ``resume_message_events`` yield ``agent_event_from_sse`` typed events +
    ``Presentation``), so the fold + persist is identical. ``events`` is the caller's
    already-constructed ``CoreEvent`` async generator.

    ``conversation_id`` is body-supplied by both callers, so the persist is bounded to a
    conversation the caller OWNS (``user_id`` + ``workspace_id``) before any write — otherwise
    a caller could inject an assistant Message / bump aggregates on another workspace's
    conversation (surfaced by the P2.4 security review; closed here for both endpoints).
    """
    final_response_text = ""
    final_trace_id = None
    final_decision: PlanOutput | None = None
    agent_steps: list[MessageAgentStep] = []
    try:
        # Send conversation_id as first event
        if conversation_id:
            cid_data = json.dumps({"event": "conversation", "conversation_id": conversation_id})
            yield f"event: conversation\ndata: {cid_data}\n\n"

        # Send the backend message_id so the frontend can reference the real ID
        mid_data = json.dumps({"event": "message_id", "message_id": assistant_message_id})
        yield f"event: message_id\ndata: {mid_data}\n\n"

        # Consume typed CoreEvents: fold them into the persisted-message
        # metadata via type matching (no bare dict["event"] sniffing), then
        # serialize each to its SSE frame for the browser.
        async for event in events:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            match event:
                case Presentation(text=text):
                    final_response_text = text
                case TraceStarted(trace_id=trace_id):
                    final_trace_id = trace_id
                case PlanReady(plan=plan):
                    if isinstance(plan, dict):
                        final_decision = PlanOutput.model_validate(plan)
                case AgentStarted(agent=agent, model=model):
                    agent_steps.append(MessageAgentStep(agent=agent, model=model))
                case AgentDone(agent=agent):
                    for step in agent_steps:
                        if step.agent == agent:
                            step.response_text = event.text
                            step.input_tokens = event.input_tokens
                            step.output_tokens = event.output_tokens
                            step.cache_creation_tokens = event.cache_creation_tokens
                            step.cache_read_tokens = event.cache_read_tokens
                            step.cost_usd = event.cost_usd
                            step.latency_ms = event.latency_ms
                            step.status = "done"
                            break
                case AgentThinking(agent=agent, text=text, is_thinking=is_thinking):
                    for step in reversed(agent_steps):
                        if step.agent != agent:
                            continue
                        if is_thinking:
                            # Extended thinking — accumulate preview (cap 2000)
                            if not step.thinking_preview:
                                step.thinking_preview = ""
                            if len(step.thinking_preview) < 2000:
                                step.thinking_preview = (step.thinking_preview + text)[:2000]
                        else:
                            # Agent reasoning text — accumulate (cap 2000)
                            if not step.reasoning_text:
                                step.reasoning_text = ""
                            if len(step.reasoning_text) < 2000:
                                step.reasoning_text = (step.reasoning_text + text)[:2000]
                        break
                case AgentToolCall(agent=agent, tool=tool, input=tool_input):
                    for step in reversed(agent_steps):
                        if step.agent == agent:
                            step.tool_calls.append(
                                MessageToolCall(
                                    tool_name=tool,
                                    tool_input=tool_input,
                                    status="success",
                                )
                            )
                            break
                case AgentToolResult(
                    agent=agent, blocked=blocked, latency_ms=latency_ms, result=tool_result
                ):
                    for step in reversed(agent_steps):
                        if step.agent == agent and step.tool_calls:
                            tc = step.tool_calls[-1]
                            if blocked:
                                tc.status = "blocked"
                            tc.duration_ms = latency_ms
                            if tool_result is not None:
                                preview = json.dumps(tool_result, default=str)
                                tc.result_preview = preview[:500] if len(preview) > 500 else preview
                            break
                case _:
                    pass

            # Serialize for the SSE client (batch-only events map to None).
            sse = core_event_to_sse(event)
            if sse is None:
                continue
            event_type = sse.get("event", "message")
            data = json.dumps(sse, default=str)
            yield f"event: {event_type}\ndata: {data}\n\n"
    except Exception as e:
        logger.error("Chat stream error: %s", e, exc_info=True)
        error_data = json.dumps(safe_error_event(e, get_correlation_id()))
        yield f"event: error\ndata: {error_data}\n\n"
    finally:
        # Save assistant response and update conversation aggregates
        if conversation_id:
            try:
                from datetime import datetime, timezone

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

                # Count: the initial turn's user message (inserted by chat_stream, not counted
                # there) + the assistant reply below. On approval-resume no new user message is
                # inserted (count_user_message=False), so only the assistant reply is counted —
                # otherwise message_count gains a phantom message per resume.
                msg_increment = 1 if count_user_message else 0
                from sqlalchemy import select, update

                async with get_session_factory()() as db:
                    # SECURITY: bound the persist to a conversation the caller OWNS. The
                    # conversation_id is body-supplied; without this ownership check a caller
                    # could inject a Message / bump aggregates on another workspace's
                    # conversation (P2.4 security review). A conversation this caller did not
                    # create is treated as "nothing to persist" — fail-safe, no cross-tenant
                    # write.
                    owned = (
                        await db.execute(
                            select(Conversation.conversation_id).where(
                                Conversation.conversation_id == conversation_id,
                                Conversation.user_id == user_id,
                                Conversation.workspace_id == workspace_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if owned is None:
                        logger.warning(
                            "Assistant reply not persisted: conversation %s not owned by "
                            "user %s / workspace %s",
                            conversation_id,
                            user_id,
                            workspace_id,
                        )
                    else:
                        if final_response_text:
                            db.add(
                                Message(
                                    message_id=assistant_message_id,
                                    conversation_id=conversation_id,
                                    workspace_id=workspace_id,
                                    role="assistant",
                                    content=final_response_text,
                                    metadata_=metadata.model_dump(mode="json"),
                                    surface=surface,
                                    trace_id=final_trace_id,
                                    input_tokens=total_input if total_input else None,
                                    output_tokens=total_output if total_output else None,
                                    cost_usd=total_cost if total_cost else None,
                                )
                            )
                            msg_increment += 1

                        # Always update conversation aggregates (timestamps, cost) so the
                        # sidebar stays accurate even when the Presenter doesn't produce a
                        # response. Workspace-scoped predicate for defense in depth.
                        await db.execute(
                            update(Conversation)
                            .where(
                                Conversation.conversation_id == conversation_id,
                                Conversation.workspace_id == workspace_id,
                            )
                            .values(
                                last_active_at=datetime.now(timezone.utc),
                                message_count=Conversation.message_count + msg_increment,
                                total_input_tokens=Conversation.total_input_tokens + total_input,
                                total_output_tokens=Conversation.total_output_tokens + total_output,
                                total_cost_usd=Conversation.total_cost_usd + total_cost,
                            )
                        )
                        await db.commit()
            except Exception:
                logger.warning("Failed to save assistant message", exc_info=True)


@router.post("/v1/muldro/chat")
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
      - error: {code, message, correlation_id}  — client-safe (no raw exception)
      - done: {trace_id}
    """
    orchestrator = await _get_orchestrator(settings, app=request.app)

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

            user_msg_id = f"msg_{ULID()}"
            async with get_session_factory()() as db:
                db.add(
                    Message(
                        message_id=user_msg_id,
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

    # Pre-generate the assistant message ID so it can be sent early via SSE
    # and reused when persisting in the finally block.
    from ulid import ULID as _ULID

    assistant_message_id = f"msg_{_ULID()}"

    # P3c: resolve the effective permission_mode HERE (interactive entry only) — omitted → the
    # per-workspace default (fail-safe auto). Pinned callers never reach this path.
    from src.models.database import get_session_factory

    resolved_permission_mode = await _resolve_request_permission_mode(
        req.permission_mode, get_session_factory(), workspace_id
    )

    return StreamingResponse(
        _stream_and_persist_chat(
            orchestrator.process_message_events(
                message=req.message,
                user_id=user_id,
                workspace_id=workspace_id,
                surface=req.surface,
                mode="ask",  # P3b: legacy planning axis retired from the API; interactive default.
                context=req.context,
                conversation_id=conversation_id,
                permission_mode=resolved_permission_mode,
            ),
            request=request,
            user_id=user_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            surface=req.surface,
            assistant_message_id=assistant_message_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class ChatResumeRequest(BaseModel):
    """Resume a chat turn that the action-time permission gate PAUSED for confirmation.

    ``decision`` is constrained to the taxonomy so a typo 422s loudly (the invoker also
    fail-closes on an unknown decision). ``reason`` is the user's optional decline/modify
    note (quoted back on a rejected write). ``conversation_id`` is the SAME conversation the
    paused turn belongs to — the continuation reply is persisted there (the pre-pause turn
    saved no assistant message: it paused before producing one).
    """

    approval_id: str
    decision: Literal["approve", "reject"]
    reason: str | None = None
    conversation_id: str | None = None
    surface: str = "web"


@router.post("/v1/muldro/chat/resume")
async def chat_resume(
    req: ChatResumeRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    settings: Settings = Depends(get_settings),
):
    """Stream the continuation of a paused chat turn after the user approves/rejects.

    The SSE counterpart of :func:`chat_stream` for the RESUME half (Step 10D P2.4): the
    initial turn paused at the permission gate, emitted ``approval_needed``, and ENDED with no
    assistant reply persisted. This endpoint re-enters the paused thread via
    ``resume_message_events`` (approve → the write fires once + a terminal reply; reject → the
    write is skipped + a decline reply), re-streams the continuation frames, and persists the
    reply via the SAME fold+persist as the initial turn (:func:`_stream_and_persist_chat`) —
    so the chat bubble is not left empty (Corr-C1). A CHAINED pause (a 2nd write in the
    resumed turn) re-emits ``approval_needed`` and the frontend re-shows the card.

    Auth is the caller's session (``get_current_user_id`` + ``get_current_workspace_id``); the
    tenant-isolation + already-decided guards live in ``resume_deep_lead``. Chat approvals are
    resumed HERE, never via ``/v1/approvals/{id}/approve|reject`` (those 409 on a chat
    approval — see ``routes_approvals._guard_not_chat_approval``).
    """
    orchestrator = await _get_orchestrator(settings, app=request.app)

    from ulid import ULID as _ULID

    assistant_message_id = f"msg_{_ULID()}"

    return StreamingResponse(
        _stream_and_persist_chat(
            orchestrator.resume_message_events(
                approval_id=req.approval_id,
                decision=req.decision,
                reason=req.reason,
                user_id=user_id,
                workspace_id=workspace_id,
                conversation_id=req.conversation_id,
            ),
            request=request,
            user_id=user_id,
            conversation_id=req.conversation_id,
            workspace_id=workspace_id,
            surface=req.surface,
            assistant_message_id=assistant_message_id,
            # Resume inserts no new user message (the original turn already did), so count
            # only the assistant continuation — avoids a phantom message per resume.
            count_user_message=False,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
