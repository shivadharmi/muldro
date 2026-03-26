"""ApprovalPolicy — capability-pattern based approval rules.

Defines which capabilities/tools require approval, at what trust tier,
and under what conditions (e.g., always, high-risk only, first-use only).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.ids import generate_id


class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"

    policy_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("apol")
    )
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_pattern: Mapped[str] = mapped_column(
        String(128), nullable=False
    )  # e.g. "email.*", "messaging.send", "*"
    trust_tier_min: Mapped[str | None] = mapped_column(
        String(16)
    )  # T0, T1, T2, T3 — minimum tier that skips approval
    approval_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="always"
    )  # always, high_risk_only, first_use, never
    risk_threshold: Mapped[str | None] = mapped_column(String(16))  # low, medium, high, critical
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_approval_policies_ws", "workspace_id"),
        Index("ix_approval_policies_ws_cap", "workspace_id", "capability_pattern"),
    )
