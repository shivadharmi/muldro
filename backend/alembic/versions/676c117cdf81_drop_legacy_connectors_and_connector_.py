"""Drop legacy connectors and connector_accounts tables

Revision ID: 676c117cdf81
Revises: 040
Create Date: 2026-03-22 04:13:54.777114
"""

from typing import Sequence, Union

from alembic import op

revision: str = "676c117cdf81"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("connector_accounts")
    op.drop_table("connectors")


def downgrade() -> None:
    # Not restoring — old model fully removed from codebase.
    pass
