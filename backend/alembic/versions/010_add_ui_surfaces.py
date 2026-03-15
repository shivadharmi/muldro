"""Add ui_surfaces table for A2UI surface state persistence.

Revision ID: 010
Revises: 009
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_surfaces",
        sa.Column("surface_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("surface_type", sa.String(32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ui_surfaces_user_type",
        "ui_surfaces",
        ["user_id", "surface_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_ui_surfaces_user_type", table_name="ui_surfaces")
    op.drop_table("ui_surfaces")
