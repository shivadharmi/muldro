from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Briefing(Base, TimestampMixin):
    __tablename__ = "briefings"

    briefing_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    briefing_date: Mapped[date] = mapped_column(Date, nullable=False)
    headline: Mapped[str | None] = mapped_column(String(512))
    top_priorities: Mapped[dict | None] = mapped_column(JSONB)
    changes_since_last: Mapped[dict | None] = mapped_column(JSONB)
    pending_approvals: Mapped[dict | None] = mapped_column(JSONB)
    recommended_actions: Mapped[dict | None] = mapped_column(JSONB)
    full_text: Mapped[str | None] = mapped_column(Text)
    search_vector = mapped_column(TSVECTOR, nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")

    __table_args__ = (Index("ix_briefings_user_date", "user_id", "briefing_date"),)
