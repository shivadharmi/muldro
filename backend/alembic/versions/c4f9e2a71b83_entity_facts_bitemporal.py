"""entity_facts bi-temporal attribute belief store

Revision ID: c4f9e2a71b83
Revises: b3e8c1f5a9d2
Create Date: 2026-07-06 00:00:00.000000

Net-new, empty table hanging off entities.entity_id (FK CASCADE). Versioned
attribute beliefs with valid_from/valid_to supersede (spec §6 Step 4 / §4.6 items
3-5). Additive: no existing data migrated; entities.attributes stays the current
snapshot. Indexes are also declared on the ORM model so `alembic check` stays clean.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4f9e2a71b83"
down_revision: Union[str, None] = "b3e8c1f5a9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_facts",
        sa.Column("fact_id", sa.String(length=64), primary_key=True),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("attr_key", sa.String(length=128), nullable=False),
        sa.Column("attr_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("corroboration_count", sa.Integer(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(["entity_id"], ["entities.entity_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_entity_facts_lookup",
        "entity_facts",
        ["entity_id", "attr_key", "valid_to"],
    )
    op.create_index("ix_entity_facts_ws", "entity_facts", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_entity_facts_ws", table_name="entity_facts")
    op.drop_index("ix_entity_facts_lookup", table_name="entity_facts")
    op.drop_table("entity_facts")
