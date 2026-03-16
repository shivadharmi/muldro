"""Execution convergence — artifact provenance FKs and task_runs index.

Revision ID: 019
Revises: 018
Create Date: 2026-03-17
"""

import sqlalchemy as sa

from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add provenance columns to artifacts
    op.add_column("artifacts", sa.Column("run_id", sa.String(64), nullable=True))
    op.add_column("artifacts", sa.Column("step_id", sa.String(64), nullable=True))
    op.add_column("artifacts", sa.Column("task_id", sa.String(64), nullable=True))
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifacts_step_id", "artifacts", ["step_id"])
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"])

    # Add index on task_runs.task_id_ref for standalone task linkage
    op.create_index("ix_task_runs_task_id_ref", "task_runs", ["task_id_ref"])


def downgrade() -> None:
    op.drop_index("ix_task_runs_task_id_ref")
    op.drop_index("ix_artifacts_task_id")
    op.drop_index("ix_artifacts_step_id")
    op.drop_index("ix_artifacts_run_id")
    op.drop_column("artifacts", "task_id")
    op.drop_column("artifacts", "step_id")
    op.drop_column("artifacts", "run_id")
