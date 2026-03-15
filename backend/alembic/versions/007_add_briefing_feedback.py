"""Add briefing_feedback table for learning loop.

Revision ID: 007
Revises: 006
Create Date: 2026-03-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "briefing_feedback",
        sa.Column("feedback_id", sa.String(64), primary_key=True),
        sa.Column("briefing_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("feedback_type", sa.String(32), nullable=False),
        sa.Column("rating", sa.Integer, nullable=True),
        sa.Column("item_section", sa.String(64), nullable=True),
        sa.Column("item_index", sa.Integer, nullable=True),
        sa.Column("item_title", sa.String(512), nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("extra_data", JSONB, nullable=True),
        sa.Column("signal_weight", sa.Float, nullable=False, server_default="1.0"),
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
    op.create_index("ix_bf_briefing_id", "briefing_feedback", ["briefing_id"])
    op.create_index("ix_bf_user_briefing", "briefing_feedback", ["user_id", "briefing_id"])
    op.create_index("ix_bf_user_type", "briefing_feedback", ["user_id", "feedback_type"])


def downgrade() -> None:
    op.drop_table("briefing_feedback")
