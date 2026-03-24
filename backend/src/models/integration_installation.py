"""IntegrationInstallation — DB-backed integration server configuration.

Replaces the static mcp_config.py with workspace-scoped installation records.
Each row represents one MCP server installed for a workspace.
"""

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class IntegrationInstallation(Base, TimestampMixin):
    __tablename__ = "connector_installations"

    install_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("inst")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, default="stdio"
    )  # stdio | sse | streamable-http
    command: Mapped[str | None] = mapped_column(String(512))
    args: Mapped[dict | None] = mapped_column(JSONB)  # stored as JSON array
    env_template: Mapped[dict | None] = mapped_column(JSONB)  # env var names → descriptions
    remote_url: Mapped[str | None] = mapped_column(String(512))
    trust_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("server_trust_records.trust_id", ondelete="SET NULL"),
    )
    auth_provider: Mapped[str | None] = mapped_column(String(64))  # oauth, token, none
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active"
    )  # active, paused, error, disabled
    health_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )  # healthy, degraded, unavailable, unknown
    scopes_granted: Mapped[list[str] | None] = mapped_column(ARRAY(String(128)))
    config: Mapped[dict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_inst_workspace", "workspace_id"),
        Index("ix_inst_ws_server", "workspace_id", "server_name", unique=True),
        Index("ix_inst_status", "workspace_id", "status"),
    )
