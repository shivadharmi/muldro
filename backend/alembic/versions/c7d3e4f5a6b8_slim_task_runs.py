"""slim task_runs — drop context_pack_json + policy_decision (extracted to task_run_details)

Revision ID: c7d3e4f5a6b8
Revises: b6c2d3e4f5a7
Create Date: 2026-07-06 00:00:02.000000

Contract phase (Step 5 §4.8, D-C4). The detail table is authoritative (dual-write +
dual-read landed). downgrade re-adds both nullable columns and copies data back from
task_run_details.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c7d3e4f5a6b8"
down_revision: Union[str, None] = "b6c2d3e4f5a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("task_runs", "context_pack_json")
    op.drop_column("task_runs", "policy_decision")


def downgrade() -> None:
    op.add_column(
        "task_runs",
        sa.Column("policy_decision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "task_runs",
        sa.Column("context_pack_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        """
        UPDATE task_runs t
        SET policy_decision = d.policy_decision,
            context_pack_json = d.context_pack
        FROM task_run_details d
        WHERE t.run_id = d.run_id
        """
    )
