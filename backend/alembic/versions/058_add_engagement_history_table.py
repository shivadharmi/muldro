"""add engagement_history table

Revision ID: 058
Revises: 057
Create Date: 2026-04-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "058"
down_revision: str | None = "057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engagement_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("signal_source", sa.String(64), nullable=False),
        sa.Column("signal_category", sa.String(64), nullable=False),
        sa.Column("engaged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dismissed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ignored_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "consecutive_dismissals", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "engagement_rate", sa.Float(), nullable=False, server_default="0.5"
        ),
        sa.Column("last_engaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "signal_source",
            "signal_category",
            name="uq_engagement_ws_source_cat",
        ),
    )
    op.create_index(
        "ix_engagement_workspace", "engagement_history", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_engagement_workspace", table_name="engagement_history")
    op.drop_table("engagement_history")
