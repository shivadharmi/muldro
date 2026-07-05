"""Task graph execution models — DAG-based durable execution engine."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class TaskRun(Base, TimestampMixin):
    """Unified execution record — tracks every interaction and plan execution.

    Lightweight runs (source='user_message') have no plan_id.
    Plan-backed runs (source='plan') link to a Plan via plan_id.
    """

    __tablename__ = "task_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending, running, paused, awaiting_approval, completed, failed, cancelled
    source: Mapped[str] = mapped_column(String(32), default="plan")
    # user_message, event, schedule, trigger, plan
    execution_mode: Mapped[str | None] = mapped_column(String(32))
    # auto_execute, approval_required, blocked, suggest_only
    policy_decision: Mapped[dict | None] = mapped_column(JSONB)
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    graph_definition: Mapped[dict | None] = mapped_column(JSONB)
    current_step_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    checkpoint: Mapped[dict | None] = mapped_column(JSONB)
    task_id_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_version: Mapped[str | None] = mapped_column(String(32))
    planner_version: Mapped[str | None] = mapped_column(String(32))
    verifier_version: Mapped[str | None] = mapped_column(String(32))
    context_pack_json: Mapped[dict | None] = mapped_column(JSONB)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSONB)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Observability rollup (populated by GraphExecutor on completion from the
    # linked Trace). Kept as denormalized columns so history views do not
    # require a JOIN for the common case of listing runs with cost/token
    # summaries.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    steps: Mapped[list["TaskStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_task_runs_user_status", "user_id", "status", "created_at"),
        Index("ix_task_runs_source", "source", "created_at"),
        Index("ix_task_runs_ws_status", "workspace_id", "status"),
        # Composite (workspace_id, idempotency_key), NOT global: task-run keys
        # carry no workspace component, so a global unique index would let one
        # workspace's run block another's on a shared key (cross-tenant).
        Index(
            "ix_task_runs_idempotency",
            "workspace_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text(
                "idempotency_key IS NOT NULL AND status NOT IN ('completed', 'failed', 'cancelled')"
            ),
        ),
    )


class TaskStep(Base, TimestampMixin):
    __tablename__ = "task_steps"

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("task_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_task_id: Mapped[str | None] = mapped_column(String(64))
    step_order: Mapped[int | None] = mapped_column(Integer)
    step_type: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(256))
    depends_on: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending, ready, running, completed, failed, skipped
    input_data: Mapped[dict | None] = mapped_column(JSONB)
    output_data: Mapped[dict | None] = mapped_column(JSONB)
    input_schema: Mapped[dict | None] = mapped_column(JSONB)
    artifact_refs: Mapped[list[str] | None] = mapped_column(ARRAY(String(512)))
    error: Mapped[dict | None] = mapped_column(JSONB)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped["TaskRun"] = relationship(back_populates="steps")

    __table_args__ = (Index("ix_task_steps_run_status", "run_id", "status"),)


class TaskRunDetail(Base, TimestampMixin):
    """1:1 side table for audience-specific run fields extracted off the hot TaskRun
    row (Step 5 §4.8). Owns policy_decision (durable, evidence/audit) and context_pack
    (heavy, ephemeral working context — by-ref + TTL via context_pack_expires_at with a
    dereference/expiry render fallback). run_id is the PK (structural 1:1)."""

    __tablename__ = "task_run_details"

    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("task_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    policy_decision: Mapped[dict | None] = mapped_column(JSONB)
    context_pack: Mapped[dict | None] = mapped_column(JSONB)
    context_pack_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_task_run_details_ws", "workspace_id"),
        Index("ix_task_run_details_ctx_expiry", "context_pack_expires_at"),
    )


class TaskCheckpoint(Base):
    __tablename__ = "task_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("task_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str | None] = mapped_column(String(64))
    state_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(String(128))
    # step_completed, approval_gate, error_retry, manual_pause
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    __table_args__ = (Index("ix_task_checkpoints_run", "run_id", "created_at"),)
