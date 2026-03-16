"""Task graph execution models — DAG-based durable execution engine."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class TaskRun(Base, TimestampMixin):
    __tablename__ = "task_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending, running, paused, awaiting_approval, completed, failed, cancelled
    graph_definition: Mapped[dict | None] = mapped_column(JSONB)
    current_step_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    checkpoint: Mapped[dict | None] = mapped_column(JSONB)
    task_id_ref: Mapped[str | None] = mapped_column(String(64))
    runtime_version: Mapped[str | None] = mapped_column(String(32))
    planner_version: Mapped[str | None] = mapped_column(String(32))
    verifier_version: Mapped[str | None] = mapped_column(String(32))
    context_pack_json: Mapped[dict | None] = mapped_column(JSONB)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSONB)

    steps: Mapped[list["TaskStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_task_runs_user_status", "user_id", "status", "created_at"),)


class TaskStep(Base, TimestampMixin):
    __tablename__ = "task_steps"

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("task_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step_order: Mapped[int | None] = mapped_column(Integer)
    step_type: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(256))
    depends_on: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending, ready, running, completed, failed, skipped
    input_data: Mapped[dict | None] = mapped_column(JSONB)
    output_data: Mapped[dict | None] = mapped_column(JSONB)
    artifact_refs: Mapped[list[str] | None] = mapped_column(ARRAY(String(512)))
    error: Mapped[dict | None] = mapped_column(JSONB)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped["TaskRun"] = relationship(back_populates="steps")

    __table_args__ = (Index("ix_task_steps_run_status", "run_id", "status"),)


class TaskCheckpoint(Base):
    __tablename__ = "task_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("task_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str | None] = mapped_column(String(64))
    state_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(String(128))
    # step_completed, approval_gate, error_retry, manual_pause
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    __table_args__ = (Index("ix_task_checkpoints_run", "run_id", "created_at"),)
