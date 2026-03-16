"""Drop legacy tables and redundant columns.

- Drop Execution and ExecutionTaskRun tables (superseded by TaskRun/TaskStep)
- Drop redundant workspace_id from tasks and notifications
  (user_id is the tenant boundary; workspace_id on these tables was never used)

Revision ID: 028
Revises: 027
"""

import sqlalchemy as sa
from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop legacy execution tables
    op.drop_table("execution_task_runs")
    op.drop_table("executions")

    # Drop redundant workspace_id from data tables
    # (user_id already provides isolation; workspace_id was never queried)
    op.drop_column("tasks", "workspace_id")
    op.drop_column("notifications", "workspace_id")


def downgrade() -> None:
    # Restore workspace_id columns
    op.add_column("notifications", sa.Column("workspace_id", sa.String(64), nullable=True))
    op.add_column("tasks", sa.Column("workspace_id", sa.String(64), nullable=True))

    # Restore legacy execution tables
    op.create_table(
        "executions",
        sa.Column("execution_id", sa.String(64), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("plans.plan_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("current_task_id", sa.String(64)),
        sa.Column("errors", sa.dialects.postgresql.JSONB),
        sa.Column("audit_ref", sa.String(128)),
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

    op.create_table(
        "execution_task_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "execution_id",
            sa.String(64),
            sa.ForeignKey("executions.execution_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("artifact_ref", sa.String(512)),
        sa.Column("result_data", sa.dialects.postgresql.JSONB),
        sa.Column("error_message", sa.Text),
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
