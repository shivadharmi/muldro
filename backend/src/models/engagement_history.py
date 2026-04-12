"""Engagement history for proactive insight surfaces.

Tracks per signal_source × signal_category how often the user engages,
dismisses, or ignores insight surfaces. Drives suppression rules.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID

from src.models.base import Base, TimestampMixin


class EngagementHistory(Base, TimestampMixin):
    __tablename__ = "engagement_history"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"eng_{ULID()}")
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    signal_source: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_category: Mapped[str] = mapped_column(String(64), nullable=False)
    engaged_count: Mapped[int] = mapped_column(Integer, default=0)
    dismissed_count: Mapped[int] = mapped_column(Integer, default=0)
    ignored_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_dismissals: Mapped[int] = mapped_column(Integer, default=0)
    engagement_rate: Mapped[float] = mapped_column(Float, default=0.5)
    last_engaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "signal_source",
            "signal_category",
            name="uq_engagement_ws_source_cat",
        ),
        Index("ix_engagement_workspace", "workspace_id"),
    )
