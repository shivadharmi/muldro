"""Add embedding column to entities for fuzzy dedup.

Revision ID: 024
Revises: 023
Create Date: 2026-03-17
"""

from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE entities ADD COLUMN IF NOT EXISTS embedding vector(1024)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_entities_embedding "
        "ON entities USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_entities_embedding")
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS embedding")
