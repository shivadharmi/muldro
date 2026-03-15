"""Procedures — learned reusable workflow patterns."""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Procedure(Base, TimestampMixin):
    __tablename__ = "procedures"

    procedure_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    trigger_pattern: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    task_template: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    learned_from: Mapped[list | None] = mapped_column(ARRAY(String(64)), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft"
    )  # active, draft, archived

    __table_args__ = (Index("ix_procedures_user_status", "user_id", "status"),)
