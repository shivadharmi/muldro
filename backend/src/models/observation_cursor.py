from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class ObservationCursor(Base, TimestampMixin):
    __tablename__ = "observation_cursors"

    cursor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # gmail, calendar, slack, github
    cursor_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # last_history_id, sync_token, oldest_ts, since_timestamp
    cursor_value: Mapped[str] = mapped_column(String(512), nullable=False)
    last_observation_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "source", name="uq_cursor_user_source"),
        Index("ix_cursor_user_source", "user_id", "source"),
    )
