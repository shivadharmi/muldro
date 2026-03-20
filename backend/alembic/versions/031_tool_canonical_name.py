"""Add canonical_name column to tool_definitions.

Revision ID: 031_tool_canonical_name
"""

from alembic import op
import sqlalchemy as sa


revision = "031_tool_canonical_name"
down_revision = "030_memory_access_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_tool_defs_canonical", table_name="tool_definitions")
    op.drop_column("tool_definitions", "canonical_name")
