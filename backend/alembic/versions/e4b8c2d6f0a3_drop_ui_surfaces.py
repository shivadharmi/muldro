"""drop ui_surfaces

The table that persisted a rendered view per user. Its replacement is nothing,
and that is the point: nothing expires, and a view is a pure function of a live
domain row, so there is no view to persist and no expiry to keep. The
``expires_at`` column goes with the table — a feed entry must never inherit a
TTL from a stored copy of how it once looked. Anything a client used to recover
from a replayed surface it now recovers by re-reading the live row over REST.

Pre-launch: there are no real users and no production data, so no rows are being
discarded that anyone can miss. ``downgrade()`` recreates the empty table so the
chain stays reversible; it does not and cannot restore rows.

Revision ID: e4b8c2d6f0a3
Revises: c1a7f0e9d4b2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e4b8c2d6f0a3"
down_revision: Union[str, None] = "c1a7f0e9d4b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_ui_surfaces_user_type", table_name="ui_surfaces")
    op.drop_table("ui_surfaces")


def downgrade() -> None:
    op.create_table(
        "ui_surfaces",
        sa.Column("surface_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("surface_type", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("detail_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("surface_id"),
    )
    op.create_index(
        "ix_ui_surfaces_user_type", "ui_surfaces", ["user_id", "surface_type"], unique=False
    )
