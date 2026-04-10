"""drop agent_routes table

Revision ID: d4cc1055c70c
Revises: 6bc73e13a801
Create Date: 2026-04-11
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4cc1055c70c"
down_revision: Union[str, None] = "6bc73e13a801"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_agent_routes_decision_type", table_name="agent_routes")
    op.drop_index("ix_agent_routes_enabled", table_name="agent_routes")
    op.drop_index("ix_agent_routes_priority", table_name="agent_routes")
    op.drop_table("agent_routes")


def downgrade() -> None:
    # Not reversible — table and data are permanently dropped
    pass
