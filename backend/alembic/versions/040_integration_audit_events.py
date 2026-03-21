"""Integration audit events.

Revision ID: 040
Revises: 039
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_audit_events",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("server_name", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("trust_tier", sa.String(4), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(128)),
        sa.Column("input_redacted", JSONB),
        sa.Column("output_summary", sa.String(2048)),
        sa.Column("status", sa.String(32), nullable=False, server_default="success"),
        sa.Column("error_message", sa.String(1024)),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("run_id", sa.String(64)),
        sa.Column("step_id", sa.String(64)),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
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
    op.create_index("ix_iaud_workspace", "integration_audit_events", ["workspace_id"])
    op.create_index(
        "ix_iaud_ws_server", "integration_audit_events", ["workspace_id", "server_name"]
    )
    op.create_index(
        "ix_iaud_ws_action", "integration_audit_events", ["workspace_id", "action"]
    )
    op.create_index(
        "ix_iaud_ws_occurred", "integration_audit_events", ["workspace_id", "occurred_at"]
    )
    op.create_index(
        "ix_iaud_ws_status", "integration_audit_events", ["workspace_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("integration_audit_events")
