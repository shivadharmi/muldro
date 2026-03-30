"""drop working_memory table

Revision ID: 048
Revises: 047
Create Date: 2026-03-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("working_memory")


def downgrade() -> None:
    op.create_table(
        "working_memory",
        sa.Column("entry_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("entry_type", sa.String(32), nullable=False, server_default="variable"),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", JSONB, nullable=True),
        sa.Column("ttl_seconds", sa.Integer, nullable=False, server_default="3600"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index("ix_wm_user_session", "working_memory", ["user_id", "session_id"])
    op.create_index("ix_wm_user_key", "working_memory", ["user_id", "key"])
    op.create_index("ix_wm_expires", "working_memory", ["expires_at"])
