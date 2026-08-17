"""Model binding — tier default or per-agent override -> provider+model+params."""

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID

from src.models.base import Base, TimestampMixin


class ModelBinding(Base, TimestampMixin):
    __tablename__ = "model_bindings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"mbind_{ULID()}")
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=True
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "tier" | "agent"
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)  # tier name or agent name
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    effort: Mapped[str] = mapped_column(String(8), nullable=False, default="none")
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_model_binding_ws",
            "workspace_id",
            "scope_type",
            "scope_key",
            unique=True,
            postgresql_where=(workspace_id.isnot(None)),
        ),
        Index(
            "uq_model_binding_default",
            "scope_type",
            "scope_key",
            unique=True,
            postgresql_where=(workspace_id.is_(None)),
        ),
    )
