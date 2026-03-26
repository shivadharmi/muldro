"""Organization-level MCP server allowlists.

Controls which MCP servers users in a workspace can install. Enforced during
the onboarding flow — servers not on the allowlist are rejected for T2/T3.
"""

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class OrgAllowlist(Base, TimestampMixin):
    __tablename__ = "org_allowlists"

    allowlist_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("oal")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    server_url_pattern: Mapped[str | None] = mapped_column(String(512))
    max_trust_tier: Mapped[str] = mapped_column(String(4), nullable=False, default="T2")
    allowed_capabilities: Mapped[dict | None] = mapped_column(JSONB)
    blocked_capabilities: Mapped[dict | None] = mapped_column(JSONB)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1024))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_oal_workspace", "workspace_id"),
        Index("ix_oal_ws_server", "workspace_id", "server_name", unique=True),
        Index("ix_oal_ws_enabled", "workspace_id", "enabled"),
    )
