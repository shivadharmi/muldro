"""add trust_states and trust_ceilings tables

Revision ID: 055
Revises: 054
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trust_states",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(128), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("approved_count", sa.Integer, server_default="0"),
        sa.Column("rejected_count", sa.Integer, server_default="0"),
        sa.Column("modified_count", sa.Integer, server_default="0"),
        sa.Column("trust_level", sa.String(32), server_default="first_use"),
        sa.Column("last_decision_at", sa.DateTime(timezone=True)),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
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
    op.create_unique_constraint(
        "uq_trust_state", "trust_states", ["workspace_id", "capability", "risk_level"]
    )
    op.create_index(
        "ix_trust_state_lookup",
        "trust_states",
        ["workspace_id", "capability", "risk_level"],
    )

    op.create_table(
        "trust_ceilings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(128), nullable=False),
        sa.Column("max_level", sa.String(32), server_default="autonomous"),
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
    op.create_unique_constraint(
        "uq_trust_ceiling", "trust_ceilings", ["workspace_id", "capability"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_trust_ceiling", "trust_ceilings", type_="unique")
    op.drop_table("trust_ceilings")
    op.drop_index("ix_trust_state_lookup", table_name="trust_states")
    op.drop_constraint("uq_trust_state", "trust_states", type_="unique")
    op.drop_table("trust_states")
