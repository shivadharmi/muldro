"""Resize embedding vector column from 1536 to 1024 dimensions.

Revision ID: 004
Revises: 003
Create Date: 2026-03-14
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop existing HNSW index (can't alter column with index)
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding")
    # Clear existing embeddings (dimension mismatch would cause errors)
    op.execute("UPDATE memories SET embedding = NULL")
    # Alter column dimension
    op.execute("ALTER TABLE memories ALTER COLUMN embedding TYPE vector(1024)")
    # Recreate HNSW index
    op.execute(
        "CREATE INDEX ix_memories_embedding ON memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding")
    op.execute("UPDATE memories SET embedding = NULL")
    op.execute("ALTER TABLE memories ALTER COLUMN embedding TYPE vector(1536)")
    op.execute(
        "CREATE INDEX ix_memories_embedding ON memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )
