"""Working memory — session-scoped ephemeral state for active task context."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class WorkingMemoryEntry(Base, TimestampMixin):
    __tablename__ = "working_memory"

    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="variable"
    )  # task_focus, variable, intermediate_result, discourse_state
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_wm_user_session", "user_id", "session_id"),
        Index("ix_wm_user_key", "user_id", "key"),
        Index("ix_wm_expires", "expires_at"),
    )
