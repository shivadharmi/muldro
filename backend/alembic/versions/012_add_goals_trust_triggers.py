"""Add goals, trust scores, and trigger tables.

Revision ID: 012
Revises: 011
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Goals ────────────────────────────────────────────────
    op.create_table(
        "goals",
        sa.Column("goal_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("progress", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("related_entity_ids", ARRAY(sa.String(64)), nullable=True),
        sa.Column("metadata_", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_goals_user_status", "goals", ["user_id", "status"])

    # ── Trust Scores ─────────────────────────────────────────
    op.create_table(
        "trust_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("approved_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("trust_score", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("auto_approve_threshold", sa.Float(), server_default="0.9", nullable=False),
        sa.Column("last_decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_trust_scores_unique", "trust_scores", ["user_id", "action_type"], unique=True
    )

    # ── Triggers ─────────────────────────────────────────────
    op.create_table(
        "triggers",
        sa.Column("trigger_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("conditions", JSONB(), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("action_config", JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("fire_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_triggers_user_enabled", "triggers", ["user_id", "enabled"])


def downgrade() -> None:
    op.drop_table("triggers")
    op.drop_table("trust_scores")
    op.drop_table("goals")
