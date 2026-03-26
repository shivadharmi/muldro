"""Rename agents.tool_scope to capability_scope.

Revision ID: 037
Revises: 036
Create Date: 2026-03-21
"""

from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("agents", "tool_scope", new_column_name="capability_scope")


def downgrade() -> None:
    op.alter_column("agents", "capability_scope", new_column_name="tool_scope")
