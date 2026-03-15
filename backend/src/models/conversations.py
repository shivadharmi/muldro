from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    surface: Mapped[str] = mapped_column(String(32), nullable=False)  # telegram, web, api
    status: Mapped[str] = mapped_column(String(16), default="active")  # active, archived
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_conversations_user_status", "user_id", "status"),)


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # user, assistant, system, tool_call, tool_result
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    surface: Mapped[str] = mapped_column(String(32), nullable=False)  # telegram, web, api

    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)
