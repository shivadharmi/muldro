"""Trigger models for reactive event-driven automation."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Trigger(Base, TimestampMixin):
    __tablename__ = "triggers"

    trigger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # {event_type, source, entity_match, importance_threshold, time_window}
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # notify, plan, escalate, procedure
    action_config: Mapped[dict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fire_count: Mapped[int] = mapped_column(Integer, default=0)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_config_json: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_triggers_user_enabled", "user_id", "enabled"),)
