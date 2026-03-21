"""Tool definition model for the tool registry."""

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class ToolDefinition(Base, TimestampMixin):
    __tablename__ = "tool_definitions"

    tool_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    description: Mapped[str | None] = mapped_column(Text)
    input_schema: Mapped[dict | None] = mapped_column(JSONB)
    output_schema: Mapped[dict | None] = mapped_column(JSONB)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    # low, medium, high, critical
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    idempotent: Mapped[bool] = mapped_column(Boolean, default=False)
    connector_type: Mapped[str | None] = mapped_column(String(32))
    # gmail, calendar, slack, github, drive, browser, internal
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    canonical_name: Mapped[str | None] = mapped_column(String(128))
    capability: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_tool_defs_connector", "connector_type"),
        Index("ix_tool_defs_risk", "risk_level"),
        Index(
            "ix_tool_defs_canonical",
            "workspace_id",
            "canonical_name",
            unique=True,
            postgresql_where="canonical_name IS NOT NULL",
        ),
        Index("ix_tool_defs_capability", "workspace_id", "capability"),
    )
