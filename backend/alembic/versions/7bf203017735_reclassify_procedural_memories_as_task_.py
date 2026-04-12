"""reclassify procedural memories as task_context

Revision ID: 7bf203017735
Revises: d9ad551a4b6b
Create Date: 2026-04-12 12:39:46.503966
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7bf203017735'
down_revision: Union[str, None] = 'd9ad551a4b6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE memories SET memory_type = 'task_context' "
        "WHERE memory_type = 'procedural'"
    )


def downgrade() -> None:
    pass  # procedural was never created, no-op
