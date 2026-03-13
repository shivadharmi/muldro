from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class NormalizedEvent(Base, TimestampMixin):
    __tablename__ = "normalized_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # gmail, calendar, slack
    source_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)  # email_received, etc.
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)  # email_thread, etc.
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    actor_entities: Mapped[dict | None] = mapped_column(JSONB)
    importance_signals: Mapped[dict | None] = mapped_column(JSONB)
    urgency_score: Mapped[float | None] = mapped_column(Float)
    importance_score: Mapped[float | None] = mapped_column(Float)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    raw_ref: Mapped[str | None] = mapped_column(String(512))
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending"
    )  # pending, processed, ignored

    __table_args__ = (
        Index("ix_events_user_occurred", "user_id", "occurred_at"),
        Index("ix_events_user_source_entity", "user_id", "source", "entity_id"),
    )
