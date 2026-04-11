"""Lightweight interaction audit — replaces TaskRun for simple interactions.

Every user message gets an InteractionLog record. Only plan-backed executions
create TaskRun records. No state machine, no TaskStep, no checkpoint.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class InteractionLog(Base):
    __tablename__ = "interaction_logs"

    interaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plan_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, server_default="0.0", nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("input_tokens", 0)
        kwargs.setdefault("output_tokens", 0)
        kwargs.setdefault("cost_usd", 0.0)
        kwargs.setdefault("latency_ms", 0)
        super().__init__(**kwargs)

    __table_args__ = (
        Index("ix_interaction_logs_ws_user", "workspace_id", "user_id", "created_at"),
        Index("ix_interaction_logs_trace", "trace_id"),
    )
