"""approval idempotency columns

Revision ID: 8af9ae555c87
Revises: c7d3e4f5a6b8
Create Date: 2026-07-08 00:57:09.602638
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '8af9ae555c87'
down_revision: Union[str, None] = 'c7d3e4f5a6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("thread_id", sa.String(64), nullable=True))
    op.add_column("approvals", sa.Column("tool_call_id", sa.String(128), nullable=True))
    # Backfill the deep-gate idempotency tuple from the legacy JSONB location so existing
    # pending approvals participate in the fence. ``?`` is the JSONB key-exists operator.
    op.execute(
        "UPDATE approvals SET thread_id = artifact_refs->>'thread_id', "
        "tool_call_id = artifact_refs->>'tool_call_id' WHERE artifact_refs ? 'thread_id'"
    )
    # Partial UNIQUE: only rows carrying the idempotency tuple are fenced; legacy /
    # autonomous approvals (NULL thread_id/tool_call_id) are unaffected.
    op.create_index(
        "uq_approvals_thread_tool_call",
        "approvals",
        ["workspace_id", "thread_id", "tool_call_id"],
        unique=True,
        postgresql_where=sa.text("thread_id IS NOT NULL AND tool_call_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_approvals_thread_tool_call", table_name="approvals")
    op.drop_column("approvals", "tool_call_id")
    op.drop_column("approvals", "thread_id")
