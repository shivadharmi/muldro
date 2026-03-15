from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class AgentDecisionLog(Base):
    __tablename__ = "agent_decision_logs"

    log_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    span_id: Mapped[str | None] = mapped_column(String(64))
    agent_name: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(128))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(64))
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_agent_decision_log_agent_created", "agent_name", "created_at"),)
