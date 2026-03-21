"""tool_definitions_capability

Revision ID: 033_tool_definitions_capability
Revises: 032_server_trust_and_capability_bindings
Create Date: 2026-03-21
"""

import sqlalchemy as sa
from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_definitions", sa.Column("capability", sa.String(128), nullable=True))
    op.create_index(
        "ix_tool_defs_capability",
        "tool_definitions",
        ["workspace_id", "capability"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_defs_capability", table_name="tool_definitions")
    op.drop_column("tool_definitions", "capability")
