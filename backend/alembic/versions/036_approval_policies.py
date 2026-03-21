"""036 — approval_policies table.

Revision ID: 036
Revises: 035
"""

from alembic import op
import sqlalchemy as sa

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_policies",
        sa.Column("policy_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("capability_pattern", sa.String(128), nullable=False),
        sa.Column("trust_tier_min", sa.String(16), nullable=True),
        sa.Column("approval_mode", sa.String(32), nullable=False, server_default="always"),
        sa.Column("risk_threshold", sa.String(16), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_approval_policies_ws", "approval_policies", ["workspace_id"])
    op.create_index(
        "ix_approval_policies_ws_cap",
        "approval_policies",
        ["workspace_id", "capability_pattern"],
    )


def downgrade() -> None:
    op.drop_table("approval_policies")
