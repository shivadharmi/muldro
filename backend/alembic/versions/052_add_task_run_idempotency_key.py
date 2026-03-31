"""Add idempotency_key to task_runs.

Revision ID: 052
Revises: 051
"""

import sqlalchemy as sa

from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("idempotency_key", sa.String(256), nullable=True))
    op.execute(
        "CREATE UNIQUE INDEX ix_task_runs_idempotency ON task_runs (idempotency_key) "
        "WHERE idempotency_key IS NOT NULL "
        "AND status NOT IN ('completed', 'failed', 'cancelled')"
    )


def downgrade() -> None:
    op.drop_index("ix_task_runs_idempotency", table_name="task_runs")
    op.drop_column("task_runs", "idempotency_key")
