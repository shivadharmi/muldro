"""Rename connector_installations to integration_installations

The ORM model (IntegrationInstallation) maps to 'integration_installations'
but migration 035 created the table as 'connector_installations'. This
migration aligns the physical table name with the ORM mapping.

Revision ID: 042
Revises: 041
Create Date: 2026-03-26
"""

import sqlalchemy as sa

from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only rename if old name exists (handles create_all environments)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "connector_installations" in tables and "integration_installations" not in tables:
        op.rename_table("connector_installations", "integration_installations")

        # Rename indexes to stay consistent (PostgreSQL keeps old names after rename)
        op.drop_index("ix_inst_workspace", table_name="integration_installations")
        op.drop_index("ix_inst_ws_server", table_name="integration_installations")
        op.drop_index("ix_inst_status", table_name="integration_installations")

        op.create_index(
            "ix_inst_workspace",
            "integration_installations",
            ["workspace_id"],
        )
        op.create_index(
            "ix_inst_ws_server",
            "integration_installations",
            ["workspace_id", "server_name"],
            unique=True,
        )
        op.create_index(
            "ix_inst_status",
            "integration_installations",
            ["workspace_id", "status"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "integration_installations" in tables and "connector_installations" not in tables:
        op.drop_index("ix_inst_status", table_name="integration_installations")
        op.drop_index("ix_inst_ws_server", table_name="integration_installations")
        op.drop_index("ix_inst_workspace", table_name="integration_installations")

        op.rename_table("integration_installations", "connector_installations")

        op.create_index(
            "ix_inst_workspace",
            "connector_installations",
            ["workspace_id"],
        )
        op.create_index(
            "ix_inst_ws_server",
            "connector_installations",
            ["workspace_id", "server_name"],
            unique=True,
        )
        op.create_index(
            "ix_inst_status",
            "connector_installations",
            ["workspace_id", "status"],
        )
