from datetime import date

from sqlalchemy import Date, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Briefing(Base, TimestampMixin):
    __tablename__ = "briefings"

    briefing_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    briefing_date: Mapped[date] = mapped_column(Date, nullable=False)
    headline: Mapped[str | None] = mapped_column(String(512))
    top_priorities: Mapped[dict | None] = mapped_column(JSONB)
    changes_since_last: Mapped[dict | None] = mapped_column(JSONB)
    pending_approvals: Mapped[dict | None] = mapped_column(JSONB)
    recommended_actions: Mapped[dict | None] = mapped_column(JSONB)
    full_text: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_briefings_user_date", "user_id", "briefing_date"),)
