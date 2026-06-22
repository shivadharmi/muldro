"""task_run token rollup + trace.run_id index

Revision ID: a74c2b19f301
Revises: d9ad551a4b6b
Create Date: 2026-04-20 22:45:00.000000

Adds:
  * task_runs.input_tokens, output_tokens, cost_usd — rollup cache populated
    on run completion so detail views avoid a JOIN on the Trace table.
  * traces.run_id + index — defensive fallback path so the detail endpoint
    can resolve observability metrics when task_runs.trace_id was never
    stamped (legacy runs or background jobs bypassing GraphExecutor).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a74c2b19f301"
down_revision: Union[str, None] = "7bf203017735"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_runs",
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "task_runs",
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "task_runs",
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
    )

    op.add_column("traces", sa.Column("run_id", sa.String(64), nullable=True))
    op.create_index("ix_traces_run_id", "traces", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_traces_run_id", table_name="traces")
    op.drop_column("traces", "run_id")
    op.drop_column("task_runs", "cost_usd")
    op.drop_column("task_runs", "output_tokens")
    op.drop_column("task_runs", "input_tokens")
