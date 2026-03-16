"""Add agents table for dynamic agent configuration.

Revision ID: 018
Revises: 017
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("agent_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("model_tier", sa.String(16), nullable=False, server_default="sonnet"),
        sa.Column("tool_scope", JSONB, nullable=False, server_default="[]"),
        sa.Column("max_tokens", sa.Integer, nullable=False, server_default="4096"),
        sa.Column("temperature", sa.Float, nullable=False, server_default="0.3"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
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
    op.create_index("ix_agents_name", "agents", ["name"])
    op.create_index("ix_agents_enabled", "agents", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_agents_enabled")
    op.drop_index("ix_agents_name")
    op.drop_table("agents")
