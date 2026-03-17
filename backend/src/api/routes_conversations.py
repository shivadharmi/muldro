"""Conversation CRUD endpoints — list, create, get, update, archive, messages."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_current_workspace_id, get_db
from src.models.conversations import Conversation, Message
from src.orchestrator.contracts import MessageMetadata

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Response Models ──────────────────────────────────────────────


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str | None = None
    status: str
    surface: str
    last_active_at: str | None = None
    message_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    preview: str | None = None
    created_at: str | None = None


class ConversationDetailResponse(ConversationSummary):
    """Full conversation detail with computed fields."""

    user_id: str


class MessageResponse(BaseModel):
    message_id: str
    role: str
    content: str
    metadata_: MessageMetadata | None = None
    surface: str
    trace_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    created_at: str | None = None


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    conversation_id: str
    total: int = 0


# ── Request Models ───────────────────────────────────────────────


class ConversationCreateRequest(BaseModel):
    surface: str = "web"
    title: str | None = None


class ConversationCreateResponse(BaseModel):
    conversation_id: str


class ConversationUpdateRequest(BaseModel):
    status: str | None = None
    title: str | None = None


# ── Helpers ──────────────────────────────────────────────────────


def _conversation_to_summary(
    c: Conversation, preview: str | None = None
) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=c.conversation_id,
        title=c.title,
        status=c.status,
        surface=c.surface,
        last_active_at=c.last_active_at.isoformat() if c.last_active_at else None,
        message_count=c.message_count,
        total_input_tokens=c.total_input_tokens,
        total_output_tokens=c.total_output_tokens,
        total_cost_usd=float(c.total_cost_usd) if c.total_cost_usd else 0.0,
        preview=preview,
        created_at=c.created_at.isoformat() if c.created_at else None,
    )


def _message_to_response(m: Message) -> MessageResponse:
    # Parse JSONB metadata into typed Pydantic model
    metadata: MessageMetadata | None = None
    if m.metadata_:
        try:
            metadata = MessageMetadata(**m.metadata_)
        except Exception:
            logger.debug("Failed to parse message metadata for %s", m.message_id)

    return MessageResponse(
        message_id=m.message_id,
        role=m.role,
        content=m.content,
        metadata_=metadata,
        surface=m.surface,
        trace_id=m.trace_id,
        input_tokens=m.input_tokens,
        output_tokens=m.output_tokens,
        cost_usd=float(m.cost_usd) if m.cost_usd else None,
        created_at=m.created_at.isoformat() if m.created_at else None,
    )


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/v1/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
    limit: int = 50,
):
    """List user's conversations, newest first.

    By default excludes archived. Pass `status=archived` to list only archived.
    """
    stmt = select(Conversation).where(
        Conversation.user_id == user_id,
        Conversation.workspace_id == workspace_id,
    )
    if status:
        stmt = stmt.where(Conversation.status == status)
    else:
        stmt = stmt.where(Conversation.status != "archived")

    stmt = stmt.order_by(Conversation.last_active_at.desc()).limit(limit)
    result = await db.execute(stmt)
    convos = result.scalars().all()

    summaries = []
    for c in convos:
        # Get first user message as preview
        preview_result = await db.execute(
            select(Message.content)
            .where(Message.conversation_id == c.conversation_id, Message.role == "user")
            .order_by(Message.created_at.asc())
            .limit(1)
        )
        preview_text = preview_result.scalar()
        if preview_text and len(preview_text) > 100:
            preview_text = preview_text[:100] + "..."

        summaries.append(_conversation_to_summary(c, preview=preview_text))

    return summaries


@router.post("/v1/conversations", response_model=ConversationCreateResponse)
async def create_conversation(
    req: ConversationCreateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation."""
    convo = Conversation(
        conversation_id=f"conv_{ULID()}",
        user_id=user_id,
        workspace_id=workspace_id,
        title=req.title,
        surface=req.surface,
        status="active",
        last_active_at=datetime.now(timezone.utc),
    )
    db.add(convo)
    await db.commit()
    return ConversationCreateResponse(conversation_id=convo.conversation_id)


@router.get("/v1/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    """Get a single conversation by ID."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.workspace_id == workspace_id,
        )
    )
    convo = result.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetailResponse(
        conversation_id=convo.conversation_id,
        user_id=convo.user_id,
        title=convo.title,
        status=convo.status,
        surface=convo.surface,
        last_active_at=convo.last_active_at.isoformat() if convo.last_active_at else None,
        message_count=convo.message_count,
        total_input_tokens=convo.total_input_tokens,
        total_output_tokens=convo.total_output_tokens,
        total_cost_usd=float(convo.total_cost_usd) if convo.total_cost_usd else 0.0,
        created_at=convo.created_at.isoformat() if convo.created_at else None,
    )


@router.patch("/v1/conversations/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str,
    req: ConversationUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    """Update conversation title or status."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.workspace_id == workspace_id,
        )
    )
    convo = result.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if req.status is not None:
        convo.status = req.status
    if req.title is not None:
        convo.title = req.title

    await db.commit()
    await db.refresh(convo)

    return _conversation_to_summary(convo)


@router.delete("/v1/conversations/{conversation_id}", status_code=204)
async def archive_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    """Archive a conversation (soft delete)."""
    result = await db.execute(
        update(Conversation)
        .where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.workspace_id == workspace_id,
        )
        .values(status="archived")
        .returning(Conversation.conversation_id)
    )
    if not result.scalar():
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.commit()


@router.get("/v1/conversations/{conversation_id}/messages", response_model=MessageListResponse)
async def get_conversation_messages(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    offset: int = 0,
    limit: int = 100,
):
    """Get messages for a conversation, oldest first. Supports pagination."""
    convo_result = await db.execute(
        select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.workspace_id == workspace_id,
        )
    )
    if not convo_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Total count for pagination
    count_result = await db.execute(
        select(func.count(Message.message_id)).where(
            Message.conversation_id == conversation_id
        )
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    messages = result.scalars().all()

    return MessageListResponse(
        conversation_id=conversation_id,
        messages=[_message_to_response(m) for m in messages],
        total=total,
    )


@router.get(
    "/v1/conversations/{conversation_id}/messages/{message_id}",
    response_model=MessageResponse,
)
async def get_message(
    conversation_id: str,
    message_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    """Get a single message by ID."""
    # Verify ownership via conversation
    convo_result = await db.execute(
        select(Conversation.conversation_id).where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.workspace_id == workspace_id,
        )
    )
    if not convo_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await db.execute(
        select(Message).where(
            Message.message_id == message_id,
            Message.conversation_id == conversation_id,
        )
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    return _message_to_response(msg)
