"""Add embedding vector column to memories table.

Revision ID: 002
Revises: 001
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Add embedding column (1536 dimensions for text-embedding-3-small)
    op.execute("ALTER TABLE memories ADD COLUMN embedding vector(1536)")

    # Create HNSW index for fast cosine similarity search
    op.execute(
        "CREATE INDEX ix_memories_embedding ON memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embedding")
