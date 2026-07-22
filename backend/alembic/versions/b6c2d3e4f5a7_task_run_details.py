"""task_run_details 1:1 side table + backfill

Revision ID: b6c2d3e4f5a7
Revises: a5f1c2d3e4b6
Create Date: 2026-07-06 00:00:01.000000

Expand phase (Step 5 §4.8, D-C1/D-C4): create the 1:1 side table and backfill the
existing task_runs.context_pack_json + policy_decision into it. Old columns stay until
the contract migration (c7d3e4f5a6b8). Backfilled context packs get a 30-day expiry.
Indexes also declared on the ORM model so `alembic check` stays clean.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b6c2d3e4f5a7"
down_revision: Union[str, None] = "a5f1c2d3e4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_run_details",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("policy_decision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("context_pack", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("context_pack_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["task_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_task_run_details_ws", "task_run_details", ["workspace_id"])
    op.create_index(
        "ix_task_run_details_ctx_expiry", "task_run_details", ["context_pack_expires_at"]
    )
    # Backfill from existing rows (only those carrying either field).
    op.execute(
        """
        INSERT INTO task_run_details
            (run_id, workspace_id, policy_decision, context_pack,
             context_pack_expires_at, created_at, updated_at)
        SELECT run_id, workspace_id, policy_decision, context_pack_json,
               CASE WHEN context_pack_json IS NOT NULL
                    THEN now() + interval '30 days' ELSE NULL END,
               now(), now()
        FROM task_runs
        WHERE policy_decision IS NOT NULL OR context_pack_json IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_task_run_details_ctx_expiry", table_name="task_run_details")
    op.drop_index("ix_task_run_details_ws", table_name="task_run_details")
    op.drop_table("task_run_details")
