"""Add temporal and scoring columns to entities and entity_relationships.

Revision ID: 015
Revises: 014
Create Date: 2026-03-16
"""

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Entity: tracking columns
    op.add_column(
        "entities",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "entities",
        sa.Column("interaction_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "entities",
        sa.Column("importance_score", sa.Float, nullable=False, server_default="0.0"),
    )

    # EntityRelationship: temporal edge columns
    op.add_column(
        "entity_relationships",
        sa.Column("strength", sa.Float, nullable=False, server_default="1.0"),
    )
    op.add_column(
        "entity_relationships",
        sa.Column("start_date", sa.Date, nullable=True),
    )
    op.add_column(
        "entity_relationships",
        sa.Column("end_date", sa.Date, nullable=True),
    )
    op.add_column(
        "entity_relationships",
        sa.Column(
            "active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("entity_relationships", "active")
    op.drop_column("entity_relationships", "end_date")
    op.drop_column("entity_relationships", "start_date")
    op.drop_column("entity_relationships", "strength")
    op.drop_column("entities", "importance_score")
    op.drop_column("entities", "interaction_count")
    op.drop_column("entities", "last_seen_at")
