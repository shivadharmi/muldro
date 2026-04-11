"""add interaction_logs table

Revision ID: 057
Revises: 056
Create Date: 2026-04-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "057"
down_revision: str | None = "056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interaction_logs",
        sa.Column("interaction_id", sa.String(64), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("message_preview", sa.String(500), nullable=True),
        sa.Column("plan_summary", sa.String(500), nullable=True),
        sa.Column("plan_id", sa.String(64), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("response_preview", sa.String(500), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("interaction_id"),
    )
    op.create_index(
        "ix_interaction_logs_user_id",
        "interaction_logs",
        ["user_id"],
    )
    op.create_index(
        "ix_interaction_logs_ws_user",
        "interaction_logs",
        ["workspace_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_interaction_logs_trace",
        "interaction_logs",
        ["trace_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_interaction_logs_trace", table_name="interaction_logs")
    op.drop_index("ix_interaction_logs_ws_user", table_name="interaction_logs")
    op.drop_index("ix_interaction_logs_user_id", table_name="interaction_logs")
    op.drop_table("interaction_logs")
