"""workspace-scoped event idempotency

Revision ID: e7f3a9c1b2d4
Revises: f2b9d4e7a1c8
Create Date: 2026-06-17 00:00:00.000000

Make NormalizedEvent uniqueness per-workspace instead of global (SVC-P3-3
follow-up).

`make_idempotency_key` derives the key from source/entity/message_id/event_type
with NO workspace or user component. With a global UNIQUE on idempotency_key
alone, two workspaces connecting the SAME external account (shared Gmail/Slack/
GitHub identity) mint identical keys, and the second workspace's event is
rejected as a cross-tenant duplicate — an isolation bug. The dedup queries are
already workspace-scoped in code (SVC-P3-3); this aligns the DB constraint.

Drops the global unique constraint (auto-named `normalized_events_idempotency_
key_key` by Postgres when the table was created with an unnamed
`sa.UniqueConstraint('idempotency_key')`) and replaces it with a composite
UNIQUE(workspace_id, idempotency_key). The global constraint was strictly
stronger, so no duplicate composite pairs can pre-exist — the new constraint
creates cleanly. Verify the dropped constraint name against the live schema
before deploy.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e7f3a9c1b2d4"
down_revision: Union[str, None] = "f2b9d4e7a1c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "normalized_events_idempotency_key_key",
        "normalized_events",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_norm_events_ws_idem",
        "normalized_events",
        ["workspace_id", "idempotency_key"],
    )


def downgrade() -> None:
    # Reverting requires that no two rows share an idempotency_key across
    # workspaces — true for data created before this migration, but a
    # cross-tenant collision created after it would block the downgrade.
    op.drop_constraint(
        "uq_norm_events_ws_idem",
        "normalized_events",
        type_="unique",
    )
    op.create_unique_constraint(
        "normalized_events_idempotency_key_key",
        "normalized_events",
        ["idempotency_key"],
    )
