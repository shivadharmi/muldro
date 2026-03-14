"""Schedule model — backend-owned dynamic scheduling."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Schedule(Base, TimestampMixin):
    """A schedule entry owned by the backend scheduler loop."""

    __tablename__ = "schedules"

    schedule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False)  # recurring | one_shot
    cron_expr: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)  # system | user | reflection
    priority: Mapped[str] = mapped_column(
        String(16), default="medium", nullable=False
    )  # low | medium | high

    __table_args__ = (Index("ix_sched_user_next", "user_id", "enabled", "next_run_at"),)
