"""drop dead oauth_connections table

Revision ID: c3f1a2b4d5e6
Revises: b4e7a1c28f90
Create Date: 2026-06-15 00:00:00.000000

Retires the `oauth_connections` table (TOOL-P2-2). It was dead code: the only
writer (`AuthService._upsert_oauth_connection`, reached solely from the
never-called `AuthService.complete_oauth`) would have crashed on insert because
it never set the NOT-NULL `workspace_id`, and nothing ever read the table. All
live OAuth storage and retrieval goes through `oauth_tokens` via `OAuthManager`.
The dead model + methods are removed in the same change.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3f1a2b4d5e6"
down_revision: Union[str, None] = "b4e7a1c28f90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_oauth_connections_user_provider", table_name="oauth_connections")
    op.drop_table("oauth_connections")


def downgrade() -> None:
    # Recreate the (dead) table to match the original initial-schema definition.
    op.create_table(
        "oauth_connections",
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_user_id", sa.String(length=128), nullable=True),
        sa.Column("email", sa.String(length=256), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("connection_id"),
    )
    op.create_index(
        "ix_oauth_connections_user_provider",
        "oauth_connections",
        ["user_id", "provider"],
        unique=True,
    )
