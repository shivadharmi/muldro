"""Add Postgres full-text search (tsvector + GIN) to 7 tables.

Revision ID: 045
Revises: 044
"""

from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None

# Table definitions: (table_name, tsvector_expression, trigger_columns)
FTS_TABLES = [
    (
        "memories",
        "setweight(to_tsvector('english', coalesce(NEW.fact_text, '')), 'A')",
        ["fact_text"],
    ),
    (
        "entities",
        (
            "setweight(to_tsvector('english', coalesce(NEW.canonical_name, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(NEW.entity_type, '')), 'B')"
        ),
        ["canonical_name", "entity_type"],
    ),
    (
        "normalized_events",
        (
            "setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(NEW.summary, '')), 'B')"
        ),
        ["title", "summary"],
    ),
    (
        "conversations",
        "setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A')",
        ["title"],
    ),
    (
        "messages",
        "setweight(to_tsvector('english', coalesce(NEW.content, '')), 'A')",
        ["content"],
    ),
    (
        "briefings",
        (
            "setweight(to_tsvector('english', coalesce(NEW.headline, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(NEW.full_text, '')), 'B')"
        ),
        ["headline", "full_text"],
    ),
    (
        "approvals",
        (
            "setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(NEW.summary, '')), 'B')"
        ),
        ["title", "summary"],
    ),
]


def upgrade() -> None:
    for table, tsvector_expr, _cols in FTS_TABLES:
        # 1. Add tsvector column
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS search_vector tsvector")

        # 2. Create GIN index
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_search_vector "
            f"ON {table} USING GIN (search_vector)"
        )

        # 3. Create trigger function
        fn_name = f"{table}_search_vector_update"
        op.execute(f"""
            CREATE OR REPLACE FUNCTION {fn_name}() RETURNS trigger AS $$
            BEGIN
                NEW.search_vector := {tsvector_expr};
                RETURN NEW;
            END
            $$ LANGUAGE plpgsql
        """)

        # 4. Create trigger
        op.execute(f"""
            DROP TRIGGER IF EXISTS trig_{table}_search_vector ON {table};
            CREATE TRIGGER trig_{table}_search_vector
                BEFORE INSERT OR UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION {fn_name}()
        """)

        # 5. Backfill existing rows by touching them (trigger fires on UPDATE)
        op.execute(f"""
            UPDATE {table} SET search_vector = search_vector
            WHERE search_vector IS NULL
        """)


def downgrade() -> None:
    for table, _expr, _cols in FTS_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trig_{table}_search_vector ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_search_vector_update()")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_search_vector")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS search_vector")
