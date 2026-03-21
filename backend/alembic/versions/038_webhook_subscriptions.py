"""Add webhook_subscriptions table.

Revision ID: 038
Revises: 037
Create Date: 2026-03-21
"""

import sqlalchemy as sa
from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_subscriptions",
        sa.Column("subscription_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(256), nullable=False),
        sa.Column("external_id", sa.String(256)),
        sa.Column("callback_url", sa.String(512), nullable=False),
        sa.Column("secret", sa.String(256)),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True)),
        sa.Column("delivery_count", sa.Integer, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("config", sa.dialects.postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_webhook_subs_ws", "webhook_subscriptions", ["workspace_id"])
    op.create_index(
        "ix_webhook_subs_ws_provider", "webhook_subscriptions", ["workspace_id", "provider"]
    )
    op.create_index(
        "ix_webhook_subs_ws_status", "webhook_subscriptions", ["workspace_id", "status"]
    )
    op.create_index("ix_webhook_subs_external", "webhook_subscriptions", ["external_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_subs_external")
    op.drop_index("ix_webhook_subs_ws_status")
    op.drop_index("ix_webhook_subs_ws_provider")
    op.drop_index("ix_webhook_subs_ws")
    op.drop_table("webhook_subscriptions")
