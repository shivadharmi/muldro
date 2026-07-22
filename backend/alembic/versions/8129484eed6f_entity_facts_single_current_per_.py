"""entity_facts single current per attribute partial unique

Revision ID: 8129484eed6f
Revises: 594a35f829ee
Create Date: 2026-07-22 22:55:51.328888

Enforce the bi-temporal invariant "at most one CURRENT (valid_to IS NULL) fact per
(entity_id, attr_key)". Without it a concurrent race (two workers extracting the same
entity attribute) can insert two current rows; current_fact()'s scalar_one_or_none() then
raises MultipleResultsFound, permanently breaking corroboration/supersede for that attribute.

Any pre-existing duplicates (from before the guard) are closed first — the newest
(valid_from, fact_id) is kept current, the rest are superseded to it — so the partial unique
index can be created without violating existing data.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "8129484eed6f"
down_revision: Union[str, None] = "594a35f829ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Close pre-existing duplicate current rows: keep the newest per (entity_id, attr_key),
    # supersede the rest to it. Idempotent w.r.t. the constraint that follows.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    fact_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY entity_id, attr_key
                        ORDER BY valid_from DESC, fact_id DESC
                    ) AS rn,
                    FIRST_VALUE(fact_id) OVER (
                        PARTITION BY entity_id, attr_key
                        ORDER BY valid_from DESC, fact_id DESC
                    ) AS keeper
                FROM entity_facts
                WHERE valid_to IS NULL
            )
            UPDATE entity_facts ef
            SET valid_to = now(), superseded_by = ranked.keeper
            FROM ranked
            WHERE ef.fact_id = ranked.fact_id AND ranked.rn > 1
            """
        )
    )
    op.create_index(
        "uq_entity_facts_current",
        "entity_facts",
        ["entity_id", "attr_key"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_entity_facts_current",
        table_name="entity_facts",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
