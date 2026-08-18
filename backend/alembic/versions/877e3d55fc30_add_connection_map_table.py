"""add connection_map table

Revision ID: 877e3d55fc30
Revises: 8bed72861ada
Create Date: 2026-08-16 10:04:25.641688

Hand-written to match ``src/models/connection_map.py`` exactly. The model is
registered in ``src/models/__init__.py`` in this same change, so a future
``--autogenerate`` will compare against it and see no diff; the migration is
authored by hand only to keep the revision chain explicit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "877e3d55fc30"
down_revision: str | None = "9a31161bb81d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connection_map",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("principal_id", sa.String(length=64), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("provider_account_id", sa.String(length=256), nullable=True),
        sa.Column("connection_id", sa.String(length=512), nullable=False),
        sa.Column("credential_reference", sa.String(length=512), nullable=True),
        sa.Column("granted_scopes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("connection_status", sa.String(length=32), nullable=False),
        sa.Column("account_alias", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "principal_id",
            "provider_id",
            "account_alias",
            name="uq_connection_map_principal_alias",
        ),
    )
    op.create_index("ix_connection_map_tenant_id", "connection_map", ["tenant_id"], unique=False)
    op.create_index(
        "ix_connection_map_workspace_id", "connection_map", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_connection_map_principal_id", "connection_map", ["principal_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_connection_map_principal_id", table_name="connection_map")
    op.drop_index("ix_connection_map_workspace_id", table_name="connection_map")
    op.drop_index("ix_connection_map_tenant_id", table_name="connection_map")
    op.drop_table("connection_map")
