"""Provider credential — one row per (workspace, provider). NULL workspace = deployment default."""

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID

from src.models.base import Base, TimestampMixin


class ProviderCredential(Base, TimestampMixin):
    __tablename__ = "provider_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"pcred_{ULID()}")
    # Nullable: a NULL row is the deployment default (there is no workspaces row for it).
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extra_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="untested")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_provider_cred_ws",
            "workspace_id",
            "provider",
            unique=True,
            postgresql_where=(workspace_id.isnot(None)),
        ),
        Index(
            "uq_provider_cred_default",
            "provider",
            unique=True,
            postgresql_where=(workspace_id.is_(None)),
        ),
    )
