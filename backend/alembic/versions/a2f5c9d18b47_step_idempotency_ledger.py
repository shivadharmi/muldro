"""step idempotency ledger

Revision ID: a2f5c9d18b47
Revises: b7c1e9f3a2d4
Create Date: 2026-06-28 00:00:00.000000

Per-step / per-tool idempotency ledger (Step 1). The (workspace_id,
identity_key) UNIQUE index is the exactly-once gate.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a2f5c9d18b47"
down_revision: Union[str, None] = "b7c1e9f3a2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idempotency_ledger",
        sa.Column("ledger_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("identity_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="in_flight"),
        sa.Column("provider_token", sa.String(length=256), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id"),
    )
    op.create_index(
        "ix_idempotency_ledger_ws_key",
        "idempotency_ledger",
        ["workspace_id", "identity_key"],
        unique=True,
    )
    op.create_index("ix_idempotency_ledger_run", "idempotency_ledger", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_ledger_run", table_name="idempotency_ledger")
    op.drop_index("ix_idempotency_ledger_ws_key", table_name="idempotency_ledger")
    op.drop_table("idempotency_ledger")
