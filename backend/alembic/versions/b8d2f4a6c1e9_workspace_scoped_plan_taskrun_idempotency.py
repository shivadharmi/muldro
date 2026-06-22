"""workspace-scoped plan + task_run idempotency

Revision ID: b8d2f4a6c1e9
Revises: e7f3a9c1b2d4
Create Date: 2026-06-17 00:00:00.000000

Extend the per-workspace idempotency fix (SVC-P3-3 follow-up) to the `plans` and
`task_runs` partial unique indexes, which shared the same global anti-pattern as
normalized_events: a UNIQUE on idempotency_key alone with no workspace
component, so two workspaces could collide on a shared key cross-tenant.

Both are PARTIAL unique indexes (the key is nullable) — the partial WHERE
predicates are preserved verbatim; only the column list gains a leading
workspace_id. The plan dedup query is already workspace-scoped in code; the
task_runs key is currently never populated (dormant), so this is defensive
before any code begins writing it.

The old single-column unique indexes were strictly stronger than the composite
ones, so no duplicate (workspace_id, idempotency_key) pairs can pre-exist — the
new indexes create cleanly.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b8d2f4a6c1e9"
down_revision: Union[str, None] = "e7f3a9c1b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PLANS_WHERE = sa.text("idempotency_key IS NOT NULL")
_TASK_RUNS_WHERE = sa.text(
    "idempotency_key IS NOT NULL AND status NOT IN ('completed', 'failed', 'cancelled')"
)


def upgrade() -> None:
    op.drop_index("ix_plans_idempotency_key", table_name="plans", postgresql_where=_PLANS_WHERE)
    op.create_index(
        "ix_plans_idempotency_key",
        "plans",
        ["workspace_id", "idempotency_key"],
        unique=True,
        postgresql_where=_PLANS_WHERE,
    )

    op.drop_index(
        "ix_task_runs_idempotency", table_name="task_runs", postgresql_where=_TASK_RUNS_WHERE
    )
    op.create_index(
        "ix_task_runs_idempotency",
        "task_runs",
        ["workspace_id", "idempotency_key"],
        unique=True,
        postgresql_where=_TASK_RUNS_WHERE,
    )


def downgrade() -> None:
    # Reverting requires no two rows share an idempotency_key across workspaces —
    # true for data created before this migration, but a cross-tenant collision
    # created after it would block re-creating the single-column unique indexes.
    op.drop_index(
        "ix_task_runs_idempotency", table_name="task_runs", postgresql_where=_TASK_RUNS_WHERE
    )
    op.create_index(
        "ix_task_runs_idempotency",
        "task_runs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=_TASK_RUNS_WHERE,
    )

    op.drop_index("ix_plans_idempotency_key", table_name="plans", postgresql_where=_PLANS_WHERE)
    op.create_index(
        "ix_plans_idempotency_key",
        "plans",
        ["idempotency_key"],
        unique=True,
        postgresql_where=_PLANS_WHERE,
    )
