"""Per-step / per-tool idempotency ledger — the exactly-once gate for writes.

One row per (workspace, logical write). The (workspace_id, identity_key) UNIQUE
index is the authoritative gate: a second reserve of the same identity raises
IntegrityError, which the ledger service turns into a de-dup. Workspace-scoped
(never global) so one tenant's key can never block another's — the same
convention as TaskRun/Plan/NormalizedEvent idempotency.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class IdempotencyLedgerEntry(Base, TimestampMixin):
    __tablename__ = "idempotency_ledger"

    ledger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(String(64))
    step_id: Mapped[str | None] = mapped_column(String(64))
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="in_flight", nullable=False)
    # in_flight, completed, failed
    provider_token: Mapped[str | None] = mapped_column(String(256))
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # UNCONDITIONAL unique: once a logical write's identity is recorded it
        # must never fire again for that run/step. Workspace-scoped, never global.
        Index("ix_idempotency_ledger_ws_key", "workspace_id", "identity_key", unique=True),
        Index("ix_idempotency_ledger_run", "run_id"),
    )
