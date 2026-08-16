"""ConnectionMap — maps a workspace principal to a namespaced external connection.

Records which (tenant, principal) owns which provider connection — e.g. a Gmail
account reached through a namespaced `connectionName` of the canonical form
`{tenant_id}:{principal_id}:{provider}:{account_alias}` (spec §6.3/§10).
`credential_reference` is an opaque pointer into the real secret store (never a
raw token) — this table is metadata only.
"""

from sqlalchemy import ARRAY, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class ConnectionMap(Base, TimestampMixin):
    __tablename__ = "connection_map"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("cmap")
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_account_id: Mapped[str | None] = mapped_column(String(256))
    # Namespaced connectionName, e.g. "{tenant_id}:{principal_id}:{provider}:{account_alias}".
    connection_id: Mapped[str] = mapped_column(String(512), nullable=False)
    # Opaque pointer into the real secret store — NEVER a token/credential value.
    credential_reference: Mapped[str | None] = mapped_column(String(512))
    granted_scopes: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    account_alias: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="user")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "principal_id",
            "provider_id",
            "account_alias",
            name="uq_connection_map_principal_alias",
        ),
    )
