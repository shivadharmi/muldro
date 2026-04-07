"""Add briefing lifecycle columns: pinned, snoozed_until, status.

Revision ID: 054
Revises: 053
"""

import sqlalchemy as sa

from alembic import op

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "briefings",
        sa.Column("pinned", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "briefings",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "briefings",
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("briefings", "status")
    op.drop_column("briefings", "snoozed_until")
    op.drop_column("briefings", "pinned")
