"""Runtime event model for durable event tracking."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class RuntimeEvent(Base, TimestampMixin):
    """Captures runtime lifecycle events for runs, steps, and tools.

    event_type values:
      command_received, route_selected, plan_created, run_created,
      step_started, step_completed, step_failed, step_blocked,
      approval_requested, approval_resolved,
      tool_call_started, tool_call_completed, tool_call_failed,
      fallback_selected, artifact_created, surface_created,
      agent_started, agent_completed,
      run_completed, run_failed, run_cancelled
    """

    __tablename__ = "runtime_events"

    event_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("revt")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str | None] = mapped_column(String(64))
    step_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_revt_workspace", "workspace_id"),
        Index("ix_revt_ws_run", "workspace_id", "run_id"),
        Index("ix_revt_ws_type", "workspace_id", "event_type"),
        Index("ix_revt_occurred", "workspace_id", "occurred_at"),
    )
