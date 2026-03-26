"""Schema expansion: trace_id on runs, trigger lifecycle, notification follow_up, browser gaps.

Revision ID: 023
Revises: 022
Create Date: 2026-03-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TaskRun: add trace_id for run-to-trace linking
    op.add_column("task_runs", sa.Column("trace_id", sa.String(64), nullable=True))

    # Triggers: add status lifecycle and action_plan_json
    op.add_column(
        "triggers",
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
    )
    op.add_column("triggers", sa.Column("action_plan_json", JSONB, nullable=True))

    # Notifications: add follow_up_at
    op.add_column(
        "notifications",
        sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True),
    )

    # BrowserSession: add run_id
    op.add_column("browser_sessions", sa.Column("run_id", sa.String(64), nullable=True))

    # BrowserAction: add output_json
    op.add_column("browser_actions", sa.Column("output_json", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("browser_actions", "output_json")
    op.drop_column("browser_sessions", "run_id")
    op.drop_column("notifications", "follow_up_at")
    op.drop_column("triggers", "action_plan_json")
    op.drop_column("triggers", "status")
    op.drop_column("task_runs", "trace_id")
