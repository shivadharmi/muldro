from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class CapabilityBinding(Base, TimestampMixin):
    """Maps a canonical capability to a backend implementation.

    backend_type:
      native       — built-in Python connector (Google, GitHub, etc.)
      mcp_official — official MCP server (T0/T1)
      mcp_user     — user-added MCP server (T2/T3)

    priority determines selection order when multiple backends
    can serve the same capability.
    """

    __tablename__ = "capability_bindings"

    binding_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("capb")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    backend_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # native | mcp_official | mcp_user
    backend_ref: Mapped[str] = mapped_column(
        String(256), nullable=False
    )  # connector name or server URL
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trust_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("server_trust_records.trust_id", ondelete="SET NULL"),
    )

    __table_args__ = (
        Index("ix_capbind_workspace", "workspace_id"),
        Index("ix_capbind_ws_capability", "workspace_id", "capability"),
        Index("ix_capbind_ws_family", "workspace_id", "family"),
        Index(
            "ix_capbind_ws_cap_backend",
            "workspace_id",
            "capability",
            "backend_type",
            unique=True,
        ),
    )
