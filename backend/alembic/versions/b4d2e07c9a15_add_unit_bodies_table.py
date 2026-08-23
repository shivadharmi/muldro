"""add unit_bodies table

The stored body of one perception Unit. A body costs a model call, so it cannot
be recomputed on every feed refresh — therefore it must BE a row rather than a
cached view.

There is no ``expires_at``: a body is superseded when the next message arrives,
never by a clock. Staleness is decided structurally, by comparing ``event_ids``
against the events the current poll grouped under the same ``frame_key``.

There is also no ``claim``/``lede``/``summary`` column. The lede is paragraph 1
of ``body``, computed on read; storing it separately would be a second
projection of one string, free to drift from the string it summarises.

``frame_key`` is Text rather than a bounded varchar because it embeds an opaque
external entity id, which is unbounded in practice. ``normalized_events`` makes
the same choice for the key that embeds the same value.

Hand-written to match ``src/models/unit_body.py`` column for column, so a later
``--autogenerate`` sees no diff. Authored by hand only to keep the revision
chain explicit.

Revision ID: b4d2e07c9a15
Revises: e4b8c2d6f0a3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4d2e07c9a15"
down_revision: str | None = "e4b8c2d6f0a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "unit_bodies",
        sa.Column("unit_body_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("frame_key", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("event_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("unit_body_id"),
        sa.UniqueConstraint("workspace_id", "frame_key", name="uq_unit_bodies_ws_frame"),
    )
    op.create_index("ix_unit_bodies_workspace", "unit_bodies", ["workspace_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_unit_bodies_workspace", table_name="unit_bodies")
    op.drop_table("unit_bodies")
