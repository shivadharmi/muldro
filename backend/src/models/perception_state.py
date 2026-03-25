from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class PerceptionState(Base, TimestampMixin):
    """Agent-informed perception scheduling state per source per user.

    Replaces in-memory SOURCE_INTERVALS + _last_run tracking with a durable,
    signal-driven policy store.  Signals (webhooks, user intent, agent requests)
    set ``pending_run``; the scheduler picks up due rows; after each cycle the
    agent-informed policy updates ``next_run_at``.
    """

    __tablename__ = "perception_state"

    state_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("pst")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    # --- Scheduling -----------------------------------------------------------
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="paused"
    )  # poll | push | hybrid | paused
    base_interval_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="300")
    effective_interval_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="300")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Agent policy (populated in Pass 3) -----------------------------------
    agent_interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    watch_entities: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- Health / circuit breaker ---------------------------------------------
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    circuit_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="closed"
    )  # closed | open | half_open
    circuit_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Signal tracking ------------------------------------------------------
    pending_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    signal_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # schedule | webhook | user_intent | agent
    signal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Stats ----------------------------------------------------------------
    last_event_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "source", name="uq_pst_ws_user_source"),
        Index("ix_pst_next_run", "next_run_at"),
        Index("ix_pst_user", "user_id"),
    )
