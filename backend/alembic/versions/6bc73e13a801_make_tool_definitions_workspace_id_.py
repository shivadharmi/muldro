"""Make tool_definitions.workspace_id nullable for global tools

Revision ID: 6bc73e13a801
Revises: 676c117cdf81
Create Date: 2026-03-22 04:37:36.747476
"""

from typing import Sequence, Union

from alembic import op

revision: str = "6bc73e13a801"
down_revision: Union[str, None] = "676c117cdf81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("tool_definitions", "workspace_id", nullable=True)


def downgrade() -> None:
    op.alter_column("tool_definitions", "workspace_id", nullable=False)
