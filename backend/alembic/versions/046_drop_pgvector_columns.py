"""Drop pgvector embedding columns from memories and entities.

Vector search is now handled exclusively by Qdrant. The embedding
columns in Postgres are no longer written to or read from.

Revision ID: 046
Revises: 045
"""

from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop HNSW indexes first
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding")
    op.execute("DROP INDEX IF EXISTS ix_entities_embedding")

    # Drop embedding columns
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embed_model_version")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embedding_ref")

    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS embed_model_version")


def downgrade() -> None:
    # Re-add columns (without data — embeddings would need regeneration)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS "
        "embedding vector(1024)"
    )
    op.execute(
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS "
        "embed_model_version varchar(64)"
    )
    op.execute(
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS "
        "embedding_ref varchar(128)"
    )
    op.execute(
        "ALTER TABLE entities ADD COLUMN IF NOT EXISTS "
        "embedding vector(1024)"
    )
    op.execute(
        "ALTER TABLE entities ADD COLUMN IF NOT EXISTS "
        "embed_model_version varchar(64)"
    )
