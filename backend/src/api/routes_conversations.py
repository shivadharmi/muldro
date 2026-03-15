"""Conversation CRUD endpoints — list, create, get messages, archive."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_db
from src.models.conversations import Conversation, Message

router = APIRouter()
logger = logging.getLogger(__name__)


class ConversationSummary(BaseModel):
    conversation_id: str
    status: str
    surface: str
    last_active_at: str | None
    message_count: int
    preview: str | None
    created_at: str | None


class ConversationCreateRequest(BaseModel):
    surface: str = "web"


class ConversationCreateResponse(BaseModel):
    conversation_id: str


class ConversationUpdateRequest(BaseModel):
    status: str | None = None


class MessageResponse(BaseModel):
    message_id: str
    role: str
    content: str
    metadata_: dict | None = None
    surface: str
    created_at: str | None


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    conversation_id: str


@router.get("/v1/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """List user's conversations, newest first."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id, Conversation.status != "archived")
        .order_by(Conversation.last_active_at.desc())
        .limit(50)
    )
    convos = result.scalars().all()

    summaries = []
    for c in convos:
        # Get message count
        count_result = await db.execute(
            select(func.count(Message.message_id)).where(
                Message.conversation_id == c.conversation_id
            )
        )
        msg_count = count_result.scalar() or 0

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

        summaries.append(
            ConversationSummary(
                conversation_id=c.conversation_id,
                status=c.status,
                surface=c.surface,
                last_active_at=c.last_active_at.isoformat() if c.last_active_at else None,
                message_count=msg_count,
                preview=preview_text,
                created_at=c.created_at.isoformat() if c.created_at else None,
            )
        )

    return summaries


@router.post("/v1/conversations", response_model=ConversationCreateResponse)
async def create_conversation(
    req: ConversationCreateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation."""
    convo = Conversation(
        conversation_id=f"conv_{ULID()}",
        user_id=user_id,
        surface=req.surface,
        status="active",
        last_active_at=datetime.now(timezone.utc),
    )
    db.add(convo)
    await db.commit()
    return ConversationCreateResponse(conversation_id=convo.conversation_id)


@router.get(
    "/v1/conversations/{conversation_id}/messages", response_model=MessageListResponse
)
async def get_conversation_messages(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    offset: int = 0,
    limit: int = 100,
):
    """Get messages for a conversation, oldest first."""
    # Verify ownership
    convo_result = await db.execute(
        select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if not convo_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

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
        messages=[
            MessageResponse(
                message_id=m.message_id,
                role=m.role,
                content=m.content,
                metadata_=m.metadata_,
                surface=m.surface,
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
            for m in messages
        ],
    )


@router.delete("/v1/conversations/{conversation_id}", status_code=204)
async def archive_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Archive a conversation (soft delete)."""
    result = await db.execute(
        update(Conversation)
        .where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
        )
        .values(status="archived")
        .returning(Conversation.conversation_id)
    )
    if not result.scalar():
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.commit()


@router.patch("/v1/conversations/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str,
    req: ConversationUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Update conversation status."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    convo = result.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if req.status is not None:
        convo.status = req.status

    await db.commit()

    count_result = await db.execute(
        select(func.count(Message.message_id)).where(
            Message.conversation_id == conversation_id
        )
    )
    msg_count = count_result.scalar() or 0

    return ConversationSummary(
        conversation_id=convo.conversation_id,
        status=convo.status,
        surface=convo.surface,
        last_active_at=convo.last_active_at.isoformat() if convo.last_active_at else None,
        message_count=msg_count,
        preview=None,
        created_at=convo.created_at.isoformat() if convo.created_at else None,
    )
