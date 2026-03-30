"""add unified registry columns to tool_definitions

Revision ID: 050
Revises: 049
Create Date: 2026-03-30
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "050"
down_revision: Union[str, None] = "049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 4 new columns for unified registry
    op.add_column(
        "tool_definitions",
        sa.Column("server", sa.String(64), nullable=True),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("backend", sa.String(32), server_default="external_mcp", nullable=False),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("source", sa.String(32), server_default="seed", nullable=False),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("verified", sa.Boolean, server_default=sa.false(), nullable=False),
    )

    # Drop the existing global unique constraint on name
    op.drop_constraint("tool_definitions_name_key", "tool_definitions", type_="unique")

    # Add composite unique index on (workspace_id, name)
    op.create_index(
        "ix_tool_defs_ws_name",
        "tool_definitions",
        ["workspace_id", "name"],
        unique=True,
    )

    # Add server lookup index
    op.create_index(
        "ix_tool_defs_server",
        "tool_definitions",
        ["workspace_id", "server"],
    )


def downgrade() -> None:
    # Drop new indexes
    op.drop_index("ix_tool_defs_server", table_name="tool_definitions")
    op.drop_index("ix_tool_defs_ws_name", table_name="tool_definitions")

    # Restore global unique constraint on name
    op.create_unique_constraint("tool_definitions_name_key", "tool_definitions", ["name"])

    # Drop 4 columns
    op.drop_column("tool_definitions", "verified")
    op.drop_column("tool_definitions", "source")
    op.drop_column("tool_definitions", "backend")
    op.drop_column("tool_definitions", "server")
