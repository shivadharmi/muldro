"""add plan idempotency_key column with partial unique index

Revision ID: 047
Revises: 6c0964a4f941
Create Date: 2026-03-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "047"
down_revision: Union[str, None] = "6c0964a4f941"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("idempotency_key", sa.String(256), nullable=True),
    )
    op.create_index(
        "ix_plans_idempotency_key",
        "plans",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_plans_idempotency_key", table_name="plans")
    op.drop_column("plans", "idempotency_key")
