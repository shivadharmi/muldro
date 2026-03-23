"""Add perception_state table for signal-driven perception.

Revision ID: 041
Revises: 6bc73e13a801
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "041"
down_revision = "6bc73e13a801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "perception_state",
        sa.Column("state_id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        # Scheduling
        sa.Column("mode", sa.String(16), nullable=False, server_default="paused"),
        sa.Column("base_interval_s", sa.Integer, nullable=False, server_default="300"),
        sa.Column("effective_interval_s", sa.Integer, nullable=False, server_default="300"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        # Agent policy
        sa.Column("agent_interval_s", sa.Integer, nullable=True),
        sa.Column("watch_entities", JSONB, nullable=True),
        # Health / circuit breaker
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("circuit_state", sa.String(16), nullable=False, server_default="closed"),
        sa.Column("circuit_opened_at", sa.DateTime(timezone=True), nullable=True),
        # Signal tracking
        sa.Column("pending_run", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("signal_source", sa.String(32), nullable=True),
        sa.Column("signal_at", sa.DateTime(timezone=True), nullable=True),
        # Stats
        sa.Column("last_event_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_runs", sa.Integer, nullable=False, server_default="0"),
        # Timestamps
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
    op.create_unique_constraint(
        "uq_pst_ws_user_source", "perception_state", ["workspace_id", "user_id", "source"]
    )
    op.create_index("ix_pst_next_run", "perception_state", ["next_run_at"])
    op.create_index("ix_pst_user", "perception_state", ["user_id"])


def downgrade() -> None:
    op.drop_table("perception_state")
