"""entity fts activation

Revision ID: b3e8c1f5a9d2
Revises: a2f5c9d18b47
Create Date: 2026-06-28 00:00:00.000000

Activates the pre-existing but dead entities.search_vector: backfill, a
BEFORE INSERT OR UPDATE trigger to keep it current, and a GIN index. The trigger
and function live only here (invisible to alembic autogenerate); the GIN index is
also declared on the ORM model so `alembic check` stays clean (Step 2).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3e8c1f5a9d2"
down_revision: Union[str, None] = "a2f5c9d18b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Backfill existing rows.
    op.execute(
        """
        UPDATE entities
        SET search_vector = to_tsvector(
            'english',
            coalesce(canonical_name, '') || ' ' || coalesce(entity_type, '')
        )
        """
    )
    # 2. Trigger function to keep search_vector current on write.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION entities_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector(
                'english',
                coalesce(NEW.canonical_name, '') || ' ' || coalesce(NEW.entity_type, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER entities_search_vector_trigger
        BEFORE INSERT OR UPDATE OF canonical_name, entity_type
        ON entities
        FOR EACH ROW EXECUTE FUNCTION entities_search_vector_update()
        """
    )
    # 3. GIN index for fast `search_vector @@ query`.
    op.create_index(
        "ix_entities_search_vector",
        "entities",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_entities_search_vector", table_name="entities")
    op.execute("DROP TRIGGER IF EXISTS entities_search_vector_trigger ON entities")
    op.execute("DROP FUNCTION IF EXISTS entities_search_vector_update()")
    # The search_vector column itself pre-existed this migration — leave it.
