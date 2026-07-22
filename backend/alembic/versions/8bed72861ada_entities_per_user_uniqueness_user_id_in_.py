"""entities per-user uniqueness (user_id in uq)

Revision ID: 8bed72861ada
Revises: 8129484eed6f
Create Date: 2026-07-22 23:02:44.670625

Entity identity is per-user, but uq_entities_ws_type_name keyed only on
(workspace_id, entity_type, canonical_name). World-model reads/upserts filter by BOTH
user_id AND workspace_id, so a second user in the same workspace with the same
(entity_type, canonical_name) could not insert their own entity: the workspace-scoped
constraint rejected it and the user-scoped retry lookup could not resolve the other user's
row, so upsert_entity raised (Codex PR #9, F1/P1).

Re-scope the constraint to (user_id, workspace_id, entity_type, canonical_name). This is
LOOSER than the old key (adds a column), so no existing row can violate it — no de-dupe
needed. Forward-only: rows the prior migration already merged across users stay merged.
The alias index uq_aliases_strong_ident is intentionally left workspace-scoped — a
strong identifier (email/handle) mapping to one entity per workspace is a desirable dedup
invariant, and _add_aliases already skips a strong identifier owned by another entity, so
it does not cause the insert dead-end.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "8bed72861ada"
down_revision: Union[str, None] = "8129484eed6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_entities_ws_type_name", "entities", type_="unique")
    op.create_unique_constraint(
        "uq_entities_user_ws_type_name",
        "entities",
        ["user_id", "workspace_id", "entity_type", "canonical_name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_entities_user_ws_type_name", "entities", type_="unique")
    op.create_unique_constraint(
        "uq_entities_ws_type_name",
        "entities",
        ["workspace_id", "entity_type", "canonical_name"],
    )
