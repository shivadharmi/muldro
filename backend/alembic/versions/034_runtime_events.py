"""runtime_events

Revision ID: 034_runtime_events
Revises: 033_tool_definitions_capability
Create Date: 2026-03-21
"""

import sqlalchemy as sa
from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("step_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=True),
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
    op.create_index("ix_revt_workspace", "runtime_events", ["workspace_id"])
    op.create_index("ix_revt_ws_run", "runtime_events", ["workspace_id", "run_id"])
    op.create_index("ix_revt_ws_type", "runtime_events", ["workspace_id", "event_type"])
    op.create_index("ix_revt_occurred", "runtime_events", ["workspace_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_revt_occurred", table_name="runtime_events")
    op.drop_index("ix_revt_ws_type", table_name="runtime_events")
    op.drop_index("ix_revt_ws_run", table_name="runtime_events")
    op.drop_index("ix_revt_workspace", table_name="runtime_events")
    op.drop_table("runtime_events")
