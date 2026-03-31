"""Add preview and detail_config JSONB columns to ui_surfaces.

Revision ID: 053
Revises: 052
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ui_surfaces", sa.Column("preview", JSONB, nullable=True))
    op.add_column("ui_surfaces", sa.Column("detail_config", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("ui_surfaces", "detail_config")
    op.drop_column("ui_surfaces", "preview")
