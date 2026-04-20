"""Trace and ModelCall models — persistent observability storage."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class Trace(Base):
    """Persistent record of an orchestrator intelligence cycle."""

    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    # Nullable because not every trace is tied to a TaskRun (ad-hoc traces,
    # perception traces, briefing traces). When present, enables the detail
    # endpoint to resolve observability metrics by run_id when
    # task_runs.trace_id was never stamped.
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)
    # user_message, perception_gmail, scheduled_briefing, trigger_fired, etc.
    status: Mapped[str] = mapped_column(String(32), default="running")
    # running, completed, failed
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    span_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    agents_invoked: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)))
    tools_called: Mapped[list[str] | None] = mapped_column(ARRAY(String(128)))
    context_summary: Mapped[str | None] = mapped_column(String(2000))
    final_result: Mapped[str | None] = mapped_column(String(2000))
    memory_writes: Mapped[int] = mapped_column(Integer, default=0)
    approval_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    spans_json: Mapped[dict | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)

    model_calls: Mapped[list["ModelCall"]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_traces_user_started", "user_id", "started_at"),
        Index("ix_traces_trigger", "trigger"),
        Index("ix_traces_status", "status"),
    )


class ModelCall(Base):
    """Individual LLM API call within a trace span."""

    __tablename__ = "model_calls"

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.trace_id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    tools_called: Mapped[list[str] | None] = mapped_column(ARRAY(String(128)))
    decision: Mapped[str | None] = mapped_column(String(256))
    error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    trace: Mapped["Trace"] = relationship(back_populates="model_calls")

    __table_args__ = (
        Index("ix_model_calls_agent_created", "agent_name", "created_at"),
        Index("ix_model_calls_trace", "trace_id"),
    )
