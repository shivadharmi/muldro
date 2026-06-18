"""scope observation cursor by workspace

Revision ID: a1b2c3d4e5f6
Revises: e7f3a9c1b2d4
Create Date: 2026-06-18 00:00:00.000000

Cross-tenant cursor bleed fix (P2): ``ObservationCursor`` was unique by
``(user_id, source)`` only.  A user who belongs to multiple workspaces
therefore shared ONE cursor row across workspaces — one workspace's poll
advanced the other workspace's connector stream position.

Goal: make the cursor's identity match ``PerceptionState``'s identity:
``(workspace_id, user_id, source)``.

Changes:
  - Drop unique constraint ``uq_cursor_user_source`` (user_id, source).
  - Drop supporting index ``ix_cursor_user_source`` (user_id, source).
  - Backfill: all existing rows already have a non-NULL ``workspace_id``
    (the column carries a FK + NOT NULL).  The new composite unique
    constraint therefore requires no data fixup — multiple workspaces had no
    shared rows (the old constraint prevented it per-user).  We log a count
    for observability and skip if zero rows exist.
  - Create unique constraint ``uq_cursor_ws_user_source``
    (workspace_id, user_id, source).  Postgres creates the backing btree
    index automatically; no separate ``CREATE INDEX`` is needed.

Backfill safety note: because the OLD constraint was ``(user_id, source)``,
no two rows for the same ``(user_id, source)`` could exist — each user had
at most ONE row per source, regardless of workspace.  Widening the constraint
to include ``workspace_id`` can therefore never produce a constraint violation
on existing data.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "b8d2f4a6c1e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Verify existing rows all have workspace_id set (belt-and-suspenders).
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM observation_cursors"
            " WHERE workspace_id IS NULL OR workspace_id = ''"
        )
    )
    null_count = result.scalar()
    if null_count and null_count > 0:
        # Log and abort rather than silently corrupt.
        raise RuntimeError(
            f"Cannot widen observation_cursors unique constraint: {null_count} rows have "
            "NULL/empty workspace_id. Backfill workspace_id from perception_state first."
        )

    total = conn.execute(sa.text("SELECT COUNT(*) FROM observation_cursors")).scalar()
    print(f"[migration] observation_cursors: {total} rows, all have workspace_id — safe to proceed")

    # Drop old constraint and index.
    op.drop_constraint("uq_cursor_user_source", "observation_cursors", type_="unique")
    op.drop_index("ix_cursor_user_source", table_name="observation_cursors")

    # Create new workspace-scoped constraint.
    # No separate index needed — Postgres creates a backing btree index
    # automatically to enforce the unique constraint.
    op.create_unique_constraint(
        "uq_cursor_ws_user_source",
        "observation_cursors",
        ["workspace_id", "user_id", "source"],
    )


def downgrade() -> None:
    # Reverting requires that no two rows share a (user_id, source) pair
    # across different workspaces — true for data created before this
    # migration, but a row created after it for a second workspace would
    # block the downgrade.
    #
    # The unique constraint's backing btree index is dropped automatically
    # when the constraint is dropped — no separate op.drop_index needed.
    op.drop_constraint("uq_cursor_ws_user_source", "observation_cursors", type_="unique")

    # Restore the OLD (user_id, source) unique constraint and its supporting index.
    op.create_unique_constraint(
        "uq_cursor_user_source",
        "observation_cursors",
        ["user_id", "source"],
    )
    op.create_index(
        "ix_cursor_user_source",
        "observation_cursors",
        ["user_id", "source"],
    )
