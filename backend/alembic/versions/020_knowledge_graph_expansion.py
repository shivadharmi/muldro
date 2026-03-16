"""Knowledge graph expansion — entity-memory linking.

Revision ID: 020
Revises: 019
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add entity_ids array to memories for entity-memory linking
    op.add_column(
        "memories",
        sa.Column("entity_ids", ARRAY(sa.String(64)), nullable=True),
    )
    op.create_index("ix_memories_entity_ids", "memories", ["entity_ids"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_memories_entity_ids")
    op.drop_column("memories", "entity_ids")
