"""Add schedules table for backend-owned dynamic scheduling.

Revision ID: 006
Revises: 005
Create Date: 2026-03-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("schedule_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("schedule_type", sa.String(32), nullable=False),
        sa.Column("cron_expr", sa.String(128), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("action_config", JSONB, nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False, server_default=sa.text("'medium'")),
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
    )
    op.create_index(
        "ix_sched_user_next", "schedules", ["user_id", "enabled", "next_run_at"]
    )
    # Schedules are seeded per-user at runtime via schedule_seeder.py


def downgrade() -> None:
    op.drop_index("ix_sched_user_next", table_name="schedules")
    op.drop_table("schedules")
