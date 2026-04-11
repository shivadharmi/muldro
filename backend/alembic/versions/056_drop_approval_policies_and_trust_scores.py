"""Drop approval_policies and trust_scores tables.

Revision ID: 056
Revises: 055
"""

from alembic import op

revision = "056"
down_revision = "055"


def upgrade() -> None:
    op.drop_index("ix_approval_policies_ws", table_name="approval_policies", if_exists=True)
    op.drop_index("ix_approval_policies_ws_cap", table_name="approval_policies", if_exists=True)
    op.drop_table("approval_policies")

    op.drop_index("ix_trust_scores_unique", table_name="trust_scores", if_exists=True)
    op.drop_table("trust_scores")


def downgrade() -> None:
    pass
