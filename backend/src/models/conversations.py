"""Conversation and Message SQLAlchemy models.

Conversations group messages by session. Messages store the full
exchange including typed metadata (agent steps, tool calls, thinking).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    """A user conversation session."""

    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    surface: Mapped[str] = mapped_column(String(32), nullable=False)  # web, api
    status: Mapped[str] = mapped_column(String(16), default="active")  # active, archived
    message_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0.0, server_default="0")
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    search_vector = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (Index("ix_conversations_user_status", "user_id", "status"),)


class Message(Base, TimestampMixin):
    """A single message in a conversation."""

    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Typed via contracts.MessageMetadata — validated at write time, stored as JSONB
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    surface: Mapped[str] = mapped_column(String(32), nullable=False)  # web, api
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    search_vector = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)
