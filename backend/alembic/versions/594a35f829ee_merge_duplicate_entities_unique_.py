"""merge duplicate entities + unique constraints (name + strong-alias)

Merges pre-existing duplicate entities (same workspace/type/name OR a shared strong
identifier alias — email/handle) into their oldest survivor, re-pointing every
reference, then adds the constraints that prevent them going forward. The merge is
one-time and NOT reversible (downgrade only drops the constraints).

Revision ID: 594a35f829ee
Revises: da007259d93c
Create Date: 2026-07-21 01:06:22.452525
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "594a35f829ee"
down_revision: Union[str, None] = "da007259d93c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _merge_duplicate_entities(bind) -> None:
    # --- union-find over duplicate entities ------------------------------------
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path-compress
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    entities = bind.execute(
        sa.text(
            "SELECT entity_id, workspace_id, entity_type, canonical_name, created_at "
            "FROM entities"
        )
    ).fetchall()
    created = {r.entity_id: (r.created_at, r.entity_id) for r in entities}
    for r in entities:
        find(r.entity_id)  # ensure every entity is a node

    # Union entities that share (workspace, type, canonical_name).
    by_name: dict[tuple, list[str]] = {}
    for r in entities:
        by_name.setdefault((r.workspace_id, r.entity_type, r.canonical_name), []).append(
            r.entity_id
        )
    for ids in by_name.values():
        for other in ids[1:]:
            union(ids[0], other)

    # Union entities that share a strong identifier alias (email/handle) in a workspace.
    strong = bind.execute(
        sa.text(
            "SELECT workspace_id, alias, entity_id FROM entity_aliases "
            "WHERE alias_type IN ('email', 'handle')"
        )
    ).fetchall()
    by_alias: dict[tuple, list[str]] = {}
    for r in strong:
        by_alias.setdefault((r.workspace_id, r.alias), []).append(r.entity_id)
    for ids in by_alias.values():
        for other in ids[1:]:
            union(ids[0], other)

    # --- pick keeper per cluster + re-point losers -----------------------------
    clusters: dict[str, list[str]] = {}
    for eid in list(parent.keys()):
        clusters.setdefault(find(eid), []).append(eid)

    for members in clusters.values():
        if len(members) < 2:
            continue
        # Oldest wins (created_at, then entity_id which is a time-ordered ULID).
        keeper = min(members, key=lambda e: created.get(e, (None, e)))
        losers = [m for m in members if m != keeper]
        for loser in losers:
            for tbl, col in (
                ("entity_aliases", "entity_id"),
                ("entity_relationships", "from_entity_id"),
                ("entity_relationships", "to_entity_id"),
                ("entity_facts", "entity_id"),
            ):
                bind.execute(
                    sa.text(f"UPDATE {tbl} SET {col} = :k WHERE {col} = :l"),
                    {"k": keeper, "l": loser},
                )
            bind.execute(
                sa.text(
                    "UPDATE memories SET entity_ids = array_replace(entity_ids, :l, :k) "
                    "WHERE :l = ANY(entity_ids)"
                ),
                {"k": keeper, "l": loser},
            )
        bind.execute(
            sa.text("DELETE FROM entities WHERE entity_id = ANY(:ids)"),
            {"ids": losers},
        )

    # --- dedupe rows/arrays that re-pointing may have collided -----------------
    # duplicate (entity_id, alias) rows on a keeper
    bind.execute(
        sa.text(
            "DELETE FROM entity_aliases a USING entity_aliases b "
            "WHERE a.ctid > b.ctid AND a.entity_id = b.entity_id AND a.alias = b.alias"
        )
    )
    # merge-induced self-loops + duplicate relationship triples
    bind.execute(sa.text("DELETE FROM entity_relationships WHERE from_entity_id = to_entity_id"))
    bind.execute(
        sa.text(
            "DELETE FROM entity_relationships a USING entity_relationships b "
            "WHERE a.ctid > b.ctid AND a.from_entity_id = b.from_entity_id "
            "AND a.relation_type = b.relation_type AND a.to_entity_id = b.to_entity_id"
        )
    )
    # dedupe memory entity_id arrays
    bind.execute(
        sa.text(
            "UPDATE memories SET entity_ids = "
            "(SELECT array_agg(DISTINCT e) FROM unnest(entity_ids) e) "
            "WHERE entity_ids IS NOT NULL"
        )
    )


def upgrade() -> None:
    _merge_duplicate_entities(op.get_bind())
    op.create_unique_constraint(
        "uq_entities_ws_type_name",
        "entities",
        ["workspace_id", "entity_type", "canonical_name"],
    )
    op.create_index(
        "uq_aliases_strong_ident",
        "entity_aliases",
        ["workspace_id", "alias"],
        unique=True,
        postgresql_where=sa.text("alias_type IN ('email', 'handle')"),
    )


def downgrade() -> None:
    # The merge is not reversible; only the constraints are dropped.
    op.drop_index(
        "uq_aliases_strong_ident",
        table_name="entity_aliases",
        postgresql_where=sa.text("alias_type IN ('email', 'handle')"),
    )
    op.drop_constraint("uq_entities_ws_type_name", "entities", type_="unique")
