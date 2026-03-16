"""Agent routes — intent-based dynamic routing.

Revision ID: 021
Revises: 020
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_routes",
        sa.Column("route_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("decision_type", sa.String(64), nullable=False),
        sa.Column("agent_pipeline", JSONB, nullable=False),
        sa.Column("conditions", JSONB, nullable=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("keywords", ARRAY(sa.String(64)), nullable=True),
        sa.Column("weight", sa.Float, nullable=False, server_default="1.0"),
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_agent_routes_decision_type", "agent_routes", ["decision_type"])
    op.create_index("ix_agent_routes_enabled", "agent_routes", ["enabled"])
    op.create_index("ix_agent_routes_priority", "agent_routes", ["priority"])


def downgrade() -> None:
    op.drop_index("ix_agent_routes_priority")
    op.drop_index("ix_agent_routes_enabled")
    op.drop_index("ix_agent_routes_decision_type")
    op.drop_table("agent_routes")
