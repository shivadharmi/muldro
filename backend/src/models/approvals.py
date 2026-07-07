from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # send_email, create_event, update_task, etc.
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    artifact_refs: Mapped[dict | None] = mapped_column(JSONB)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending, approved, rejected, expired
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    step_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("task_steps.step_id", ondelete="SET NULL")
    )
    run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("task_runs.run_id", ondelete="SET NULL")
    )
    requested_by: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    search_vector = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (
        Index("ix_approvals_user_status", "user_id", "status", "created_at"),
        Index("ix_approvals_run_status", "run_id", "status"),
        # Partial UNIQUE: only rows that carry the deep-gate idempotency tuple are fenced;
        # legacy/autonomous approvals (NULL thread_id/tool_call_id) are unaffected.
        Index(
            "uq_approvals_thread_tool_call",
            "workspace_id",
            "thread_id",
            "tool_call_id",
            unique=True,
            postgresql_where=text("thread_id IS NOT NULL AND tool_call_id IS NOT NULL"),
        ),
    )
