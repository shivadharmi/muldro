"""Add observation_status table.

Revision ID: 005
Revises: 004
Create Date: 2026-03-14
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "observation_status",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("items_found", sa.Integer(), default=0),
        sa.Column("items_ingested", sa.Integer(), default=0),
        sa.Column("status", sa.String(32), default="ok"),
        sa.Column("error_message", sa.String(512), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_obs_user_source", "observation_status", ["user_id", "source"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_obs_user_source", table_name="observation_status")
    op.drop_table("observation_status")
