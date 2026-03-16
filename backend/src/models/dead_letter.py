"""Dead-letter queue model — stores failed operations for retry or inspection."""

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class DeadLetterEntry(Base, TimestampMixin):
    __tablename__ = "dead_letter_queue"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )

    # What failed
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # event_processing, entity_extraction, memory_extraction,
    # plan_execution, notification, embedding

    # Reference to the source
    source_id: Mapped[str | None] = mapped_column(String(128))
    # e.g., event_id, execution_id, plan_id

    # Error details
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    error_context: Mapped[dict | None] = mapped_column(JSONB)

    # Retry tracking
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending, retrying, resolved, exhausted

    # The original payload for replay
    payload: Mapped[dict | None] = mapped_column(JSONB)

    last_attempted_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_dlq_user_status", "user_id", "status"),
        Index("ix_dlq_operation_status", "operation_type", "status"),
    )
