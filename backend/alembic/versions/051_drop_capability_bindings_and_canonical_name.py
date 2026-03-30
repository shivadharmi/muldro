"""Drop capability_bindings table and canonical_name column.

CapabilityBinding was used by the now-deleted CapabilityResolver for
multi-backend dispatch routing. The unified registry replaces this entirely.
The canonical_name column was used by the now-deleted tool name normalizer.

Revision ID: 051
Revises: 050
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop capability_bindings table
    op.drop_table("capability_bindings")

    # Drop canonical_name column and its index from tool_definitions
    op.drop_index("ix_tool_defs_canonical", table_name="tool_definitions")
    op.drop_column("tool_definitions", "canonical_name")

    # Fix NULL workspace_id uniqueness: replace single unique index with
    # two partial indexes. PostgreSQL treats NULL as distinct in unique indexes,
    # so UNIQUE(workspace_id, name) allows duplicate global tool names.
    op.drop_index("ix_tool_defs_ws_name", table_name="tool_definitions")
    op.create_index(
        "ix_tool_defs_ws_name",
        "tool_definitions",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )
    op.create_index(
        "ix_tool_defs_global_name",
        "tool_definitions",
        ["name"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )


def downgrade() -> None:
    # Restore single unique index (undo NULL-safe split)
    op.drop_index("ix_tool_defs_global_name", table_name="tool_definitions")
    op.drop_index("ix_tool_defs_ws_name", table_name="tool_definitions")
    op.create_index(
        "ix_tool_defs_ws_name",
        "tool_definitions",
        ["workspace_id", "name"],
        unique=True,
    )

    # Restore canonical_name column
    op.add_column(
        "tool_definitions",
        sa.Column("canonical_name", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_tool_defs_canonical",
        "tool_definitions",
        ["workspace_id", "canonical_name"],
        unique=True,
        postgresql_where=sa.text("canonical_name IS NOT NULL"),
    )

    # Restore capability_bindings table
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
        sa.Column("family", sa.String(64)),
        sa.Column("backend_type", sa.String(32), nullable=False),
        sa.Column("backend_ref", sa.String(256)),
        sa.Column("tool_name", sa.String(128)),
        sa.Column("priority", sa.Integer, default=50),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column(
            "trust_id",
            sa.String(64),
            sa.ForeignKey("server_trust_records.trust_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
