from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class ServerTrustRecord(Base, TimestampMixin):
    """Trust record for an MCP server.

    Trust tiers:
      T0 — internal (the FastMCP servers Muldro owns)
      T1 — official MCP (Anthropic-published or org-verified)
      T2 — org-approved (admin-allowlisted third-party)
      T3 — user-added (public MCP servers, highest restriction)
    """

    __tablename__ = "server_trust_records"

    trust_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("trs")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    server_url: Mapped[str | None] = mapped_column(String(512))
    trust_tier: Mapped[str] = mapped_column(String(4), nullable=False)  # T0, T1, T2, T3
    verified_by: Mapped[str | None] = mapped_column(String(128))
    manifest_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_audit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_server_trust_workspace", "workspace_id"),
        Index(
            "ix_server_trust_ws_name",
            "workspace_id",
            "server_name",
            unique=True,
        ),
        Index("ix_server_trust_tier", "workspace_id", "trust_tier"),
    )
