"""connector_installations

Revision ID: 035_connector_installations
Revises: 034_runtime_events
Create Date: 2026-03-21
"""

import sqlalchemy as sa
from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_installations",
        sa.Column("install_id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("server_name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False, server_default="stdio"),
        sa.Column("command", sa.String(512), nullable=True),
        sa.Column("args", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("env_template", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("remote_url", sa.String(512), nullable=True),
        sa.Column(
            "trust_id",
            sa.String(64),
            sa.ForeignKey("server_trust_records.trust_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("auth_provider", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("health_status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column(
            "scopes_granted",
            sa.dialects.postgresql.ARRAY(sa.String(128)),
            nullable=True,
        ),
        sa.Column("config", sa.dialects.postgresql.JSONB, nullable=True),
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
    op.create_index("ix_inst_workspace", "connector_installations", ["workspace_id"])
    op.create_index(
        "ix_inst_ws_server",
        "connector_installations",
        ["workspace_id", "server_name"],
        unique=True,
    )
    op.create_index(
        "ix_inst_status", "connector_installations", ["workspace_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_inst_status", table_name="connector_installations")
    op.drop_index("ix_inst_ws_server", table_name="connector_installations")
    op.drop_index("ix_inst_workspace", table_name="connector_installations")
    op.drop_table("connector_installations")
