"""Integration audit events — cross-boundary data flow tracking.

Every MCP tool call that crosses a trust boundary gets an audit record
with hashed inputs and redacted sensitive fields.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class IntegrationAuditEvent(Base, TimestampMixin):
    __tablename__ = "integration_audit_events"

    audit_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("iaud")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    trust_tier: Mapped[str] = mapped_column(String(4), nullable=False)
    action: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # tool_call, install, activate, revoke, inspect
    input_hash: Mapped[str | None] = mapped_column(String(128))
    input_redacted: Mapped[dict | None] = mapped_column(JSONB)
    output_summary: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="success"
    )  # success, failed, blocked, rate_limited
    error_message: Mapped[str | None] = mapped_column(String(1024))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(64))
    step_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index("ix_iaud_workspace", "workspace_id"),
        Index("ix_iaud_ws_server", "workspace_id", "server_name"),
        Index("ix_iaud_ws_action", "workspace_id", "action"),
        Index("ix_iaud_ws_occurred", "workspace_id", "occurred_at"),
        Index("ix_iaud_ws_status", "workspace_id", "status"),
    )
