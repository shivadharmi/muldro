"""MCP server catalog and org allowlists.

Revision ID: 039
Revises: 038
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_server_catalog",
        sa.Column("catalog_id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("server_name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("description", sa.String(2048)),
        sa.Column("publisher", sa.String(256)),
        sa.Column("source_url", sa.String(512)),
        sa.Column("transport", sa.String(32), nullable=False, server_default="stdio"),
        sa.Column("command", sa.String(512)),
        sa.Column("args_template", JSONB),
        sa.Column("env_template", JSONB),
        sa.Column("remote_url", sa.String(512)),
        sa.Column("default_trust_tier", sa.String(4), nullable=False, server_default="T3"),
        sa.Column("risk_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("risk_factors", JSONB),
        sa.Column("capabilities", ARRAY(sa.String(128))),
        sa.Column("tags", ARRAY(sa.String(64))),
        sa.Column("verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("manifest_hash", sa.String(128)),
        sa.Column("tool_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
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
    op.create_index("ix_mcat_workspace", "mcp_server_catalog", ["workspace_id"])
    op.create_index(
        "ix_mcat_ws_name", "mcp_server_catalog", ["workspace_id", "server_name"], unique=True
    )
    op.create_index("ix_mcat_ws_verified", "mcp_server_catalog", ["workspace_id", "verified"])
    op.create_index("ix_mcat_ws_tags", "mcp_server_catalog", ["workspace_id", "tags"])

    op.create_table(
        "org_allowlists",
        sa.Column("allowlist_id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("server_name", sa.String(128), nullable=False),
        sa.Column("server_url_pattern", sa.String(512)),
        sa.Column("max_trust_tier", sa.String(4), nullable=False, server_default="T2"),
        sa.Column("allowed_capabilities", JSONB),
        sa.Column("blocked_capabilities", JSONB),
        sa.Column("requires_approval", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("added_by", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(1024)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
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
    op.create_index("ix_oal_workspace", "org_allowlists", ["workspace_id"])
    op.create_index(
        "ix_oal_ws_server", "org_allowlists", ["workspace_id", "server_name"], unique=True
    )
    op.create_index("ix_oal_ws_enabled", "org_allowlists", ["workspace_id", "enabled"])


def downgrade() -> None:
    op.drop_table("org_allowlists")
    op.drop_table("mcp_server_catalog")
