from sqlalchemy import DateTime, Float, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class TokenUsage(Base):
    __tablename__ = "token_usage"

    usage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trigger: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # user_message, perception_cycle, scheduled_briefing
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_token_usage_agent_created", "agent_name", "created_at"),
        Index("ix_token_usage_trace", "trace_id"),
    )
