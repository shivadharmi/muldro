"""MCP Server Catalog — discoverable MCP servers available for installation.

Each entry represents a known MCP server that users can browse and install.
Populated by org admins or synced from public registries.
"""

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class MCPServerCatalog(Base, TimestampMixin):
    __tablename__ = "mcp_server_catalog"

    catalog_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("mcat")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048))
    publisher: Mapped[str | None] = mapped_column(String(256))
    source_url: Mapped[str | None] = mapped_column(String(512))
    transport: Mapped[str] = mapped_column(String(32), nullable=False, default="stdio")
    command: Mapped[str | None] = mapped_column(String(512))
    args_template: Mapped[dict | None] = mapped_column(JSONB)
    env_template: Mapped[dict | None] = mapped_column(JSONB)
    remote_url: Mapped[str | None] = mapped_column(String(512))
    default_trust_tier: Mapped[str] = mapped_column(String(4), nullable=False, default="T3")
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_factors: Mapped[dict | None] = mapped_column(JSONB)
    capabilities: Mapped[list[str] | None] = mapped_column(ARRAY(String(128)))
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manifest_hash: Mapped[str | None] = mapped_column(String(128))
    tool_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active"
    )  # active, deprecated, removed

    __table_args__ = (
        Index("ix_mcat_workspace", "workspace_id"),
        Index("ix_mcat_ws_name", "workspace_id", "server_name", unique=True),
        Index("ix_mcat_ws_verified", "workspace_id", "verified"),
        Index("ix_mcat_ws_tags", "workspace_id", "tags"),
    )
