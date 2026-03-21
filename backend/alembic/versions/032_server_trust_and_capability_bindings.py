"""server_trust_and_capability_bindings

Revision ID: 032_server_trust_and_capability_bindings
Revises: 031_tool_canonical_name
Create Date: 2026-03-21
"""

import sqlalchemy as sa
from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "server_trust_records",
        sa.Column("trust_id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("server_name", sa.String(128), nullable=False),
        sa.Column("server_url", sa.String(512), nullable=True),
        sa.Column("trust_tier", sa.String(4), nullable=False),
        sa.Column("verified_by", sa.String(128), nullable=True),
        sa.Column("manifest_hash", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_audit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_server_trust_workspace", "server_trust_records", ["workspace_id"])
    op.create_index(
        "ix_server_trust_ws_name",
        "server_trust_records",
        ["workspace_id", "server_name"],
        unique=True,
    )
    op.create_index(
        "ix_server_trust_tier",
        "server_trust_records",
        ["workspace_id", "trust_tier"],
    )

    op.create_table(
        "capability_bindings",
        sa.Column("binding_id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(128), nullable=False),
        sa.Column("family", sa.String(64), nullable=False),
        sa.Column("backend_type", sa.String(32), nullable=False),
        sa.Column("backend_ref", sa.String(256), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "trust_id",
            sa.String(64),
            sa.ForeignKey("server_trust_records.trust_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_capbind_workspace", "capability_bindings", ["workspace_id"])
    op.create_index(
        "ix_capbind_ws_capability", "capability_bindings", ["workspace_id", "capability"]
    )
    op.create_index("ix_capbind_ws_family", "capability_bindings", ["workspace_id", "family"])
    op.create_index(
        "ix_capbind_ws_cap_backend",
        "capability_bindings",
        ["workspace_id", "capability", "backend_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_capbind_ws_cap_backend", table_name="capability_bindings")
    op.drop_index("ix_capbind_ws_family", table_name="capability_bindings")
    op.drop_index("ix_capbind_ws_capability", table_name="capability_bindings")
    op.drop_index("ix_capbind_workspace", table_name="capability_bindings")
    op.drop_table("capability_bindings")

    op.drop_index("ix_server_trust_tier", table_name="server_trust_records")
    op.drop_index("ix_server_trust_ws_name", table_name="server_trust_records")
    op.drop_index("ix_server_trust_workspace", table_name="server_trust_records")
    op.drop_table("server_trust_records")
