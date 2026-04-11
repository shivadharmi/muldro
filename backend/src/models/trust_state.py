"""Trust state models — per-capability graduated trust tracking."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class TrustState(Base, TimestampMixin):
    """Tracks trust per (workspace, capability, risk_level) with graduation counters."""

    __tablename__ = "trust_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    modified_count: Mapped[int] = mapped_column(Integer, default=0)
    trust_level: Mapped[str] = mapped_column(String(32), default="first_use")
    last_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "capability", "risk_level", name="uq_trust_state"),
        Index("ix_trust_state_lookup", "workspace_id", "capability", "risk_level"),
    )


class TrustCeiling(Base, TimestampMixin):
    """User-set maximum autonomy level per capability."""

    __tablename__ = "trust_ceilings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    max_level: Mapped[str] = mapped_column(String(32), default="autonomous")

    __table_args__ = (UniqueConstraint("workspace_id", "capability", name="uq_trust_ceiling"),)
