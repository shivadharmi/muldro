"""repair same-prefix doubled surface ids

Revision ID: b7c1e9f3a2d4
Revises: c3d5e7f9a1b2
Create Date: 2026-06-22 00:00:00.000000

The canonical run surface id IS the run_id (already ``run_<ULID>``). A defect
re-applied the ``run_`` prefix when building the surface id, producing
``run_run_<ULID>`` — a same-prefix double. This value was persisted to
``ui_surfaces.surface_id`` (a primary key) and to
``task_runs.checkpoint->>'surface_id'``, and leaked to the UI.

This data migration repairs existing doubled values to a single prefix using the
regex ``^(\\w+)_\\1_`` → ``\\1_`` (e.g. ``run_run_X`` → ``run_X``). The
backreference ``\\1`` only matches when the two leading segments are IDENTICAL,
so it touches same-prefix doubles ONLY. Cross-prefix ids — where the two leading
segments differ, e.g. ``summary_run_X`` (summary≠run) or ``briefing_brief_X``
(briefing≠brief) — do not match and are left intact; they are intentional and
load-bearing.

Idempotent: re-running is a no-op because corrected rows no longer match the
pattern. ``downgrade()`` is intentionally a no-op (the doubled form was a bug; we
do not re-introduce it).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7c1e9f3a2d4"
down_revision: Union[str, None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Matches a same-prefix double at the start: ``<word>_<word>_`` where both words
# are identical (e.g. ``run_run_``). Cross-prefix ids do not match.
_DOUBLE_PATTERN = r"^(\w+)_\1_"
_DOUBLE_REPLACE = r"\1_"


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Repair ui_surfaces.surface_id (primary key). Guard against PK
    #    collisions: if the corrected id already exists (e.g. both the doubled
    #    and the canonical surface were persisted), delete the malformed
    #    duplicate row instead of updating it (which would violate the PK).
    conn.execute(
        sa.text(
            """
            DELETE FROM ui_surfaces bad
            WHERE bad.surface_id ~ :pattern
              AND EXISTS (
                  SELECT 1 FROM ui_surfaces good
                  WHERE good.surface_id = regexp_replace(bad.surface_id, :pattern, :replace)
              )
            """
        ),
        {"pattern": _DOUBLE_PATTERN, "replace": _DOUBLE_REPLACE},
    )
    conn.execute(
        sa.text(
            """
            UPDATE ui_surfaces
            SET surface_id = regexp_replace(surface_id, :pattern, :replace)
            WHERE surface_id ~ :pattern
            """
        ),
        {"pattern": _DOUBLE_PATTERN, "replace": _DOUBLE_REPLACE},
    )

    # 2) Repair task_runs.checkpoint->>'surface_id' (JSONB). Only rows whose
    #    embedded surface_id matches the doubled pattern are touched.
    conn.execute(
        sa.text(
            """
            UPDATE task_runs
            SET checkpoint = jsonb_set(
                checkpoint,
                '{surface_id}',
                to_jsonb(
                    regexp_replace(checkpoint->>'surface_id', :pattern, :replace)
                )
            )
            WHERE checkpoint ? 'surface_id'
              AND (checkpoint->>'surface_id') ~ :pattern
            """
        ),
        {"pattern": _DOUBLE_PATTERN, "replace": _DOUBLE_REPLACE},
    )


def downgrade() -> None:
    # No-op: the doubled surface id was a defect; we do not re-introduce it.
    pass
