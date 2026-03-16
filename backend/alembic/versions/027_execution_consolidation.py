"""Consolidate Execution into TaskRun — unified execution tracking.

- TaskRun.plan_id becomes nullable (lightweight runs don't need a plan)
- TaskRun gains: source, execution_mode, policy_decision, conversation_id
- TaskStep gains: plan_task_id FK for plan→run traceability
- Execution/ExecutionTaskRun tables kept in DB but no longer written to

Revision ID: 027
Revises: 026
Create Date: 2026-03-18
"""

import sqlalchemy as sa
from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TaskRun: make plan_id nullable (lightweight runs have no plan)
    op.alter_column("task_runs", "plan_id", existing_type=sa.String(64), nullable=True)

    # TaskRun: add source (user_message, event, schedule, trigger, plan)
    op.add_column(
        "task_runs",
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'plan'"),
        ),
    )

    # TaskRun: add execution_mode (auto_execute, approval_required, blocked)
    op.add_column(
        "task_runs",
        sa.Column("execution_mode", sa.String(32), nullable=True),
    )

    # TaskRun: add policy_decision JSONB (governor's full decision)
    op.add_column(
        "task_runs",
        sa.Column("policy_decision", sa.dialects.postgresql.JSONB(), nullable=True),
    )

    # TaskRun: add conversation_id for chat-originated runs
    op.add_column(
        "task_runs",
        sa.Column("conversation_id", sa.String(64), nullable=True),
    )

    # TaskStep: add plan_task_id for plan→run traceability
    op.add_column(
        "task_steps",
        sa.Column("plan_task_id", sa.String(64), nullable=True),
    )

    # Index for source-based queries
    op.create_index("ix_task_runs_source", "task_runs", ["source", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_task_runs_source", table_name="task_runs")
    op.drop_column("task_steps", "plan_task_id")
    op.drop_column("task_runs", "conversation_id")
    op.drop_column("task_runs", "policy_decision")
    op.drop_column("task_runs", "execution_mode")
    op.drop_column("task_runs", "source")
    op.alter_column("task_runs", "plan_id", existing_type=sa.String(64), nullable=False)
