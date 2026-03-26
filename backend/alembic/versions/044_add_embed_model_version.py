"""Add embed_model_version column to memories and entities.

Tracks which embedding model produced the vector, enabling future
re-embedding when the model changes.

Revision ID: 044
Revises: 043
Create Date: 2026-03-26
"""

import sqlalchemy as sa

from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("embed_model_version", sa.String(64), nullable=True))
    op.add_column("entities", sa.Column("embed_model_version", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("entities", "embed_model_version")
    op.drop_column("memories", "embed_model_version")
