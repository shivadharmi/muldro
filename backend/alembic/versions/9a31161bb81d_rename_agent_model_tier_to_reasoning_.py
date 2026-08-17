"""rename agent model_tier to reasoning/balanced/fast

Revision ID: 9a31161bb81d
Revises: ff8fadb23324
Create Date: 2026-08-17 11:44:30.175456
"""

from typing import Sequence, Union

from alembic import op

revision: str = "9a31161bb81d"
down_revision: Union[str, None] = "ff8fadb23324"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE agents SET model_tier = 'reasoning' WHERE model_tier = 'opus'")
    op.execute("UPDATE agents SET model_tier = 'balanced' WHERE model_tier = 'sonnet'")
    op.execute("UPDATE agents SET model_tier = 'fast' WHERE model_tier = 'haiku'")


def downgrade() -> None:
    op.execute("UPDATE agents SET model_tier = 'opus' WHERE model_tier = 'reasoning'")
    op.execute("UPDATE agents SET model_tier = 'sonnet' WHERE model_tier = 'balanced'")
    op.execute("UPDATE agents SET model_tier = 'haiku' WHERE model_tier = 'fast'")
