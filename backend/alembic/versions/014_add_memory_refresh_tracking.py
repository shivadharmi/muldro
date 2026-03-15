"""Add refresh_count and last_accessed_at to memories for stability tracking.

Revision ID: 014
Revises: 013
Create Date: 2026-03-16
"""

import sqlalchemy as sa

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("refresh_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "memories",
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memories_last_accessed", "memories", ["last_accessed_at"])


def downgrade() -> None:
    op.drop_index("ix_memories_last_accessed", "memories")
    op.drop_column("memories", "last_accessed_at")
    op.drop_column("memories", "refresh_count")
