"""Goal and trust score models."""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Goal(Base, TimestampMixin):
    __tablename__ = "goals"

    goal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    target_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")
    # active, completed, abandoned
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    related_entity_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    metadata_: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_goals_user_status", "user_id", "status"),)


class TrustScore(Base, TimestampMixin):
    __tablename__ = "trust_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    trust_score: Mapped[float] = mapped_column(Float, default=0.0)
    auto_approve_threshold: Mapped[float] = mapped_column(Float, default=0.9)
    last_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_trust_scores_unique", "user_id", "action_type", unique=True),)
