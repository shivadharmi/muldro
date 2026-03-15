"""Add oauth_tokens table for encrypted OAuth token storage.

Revision ID: 009
Revises: 008
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_tokens",
        sa.Column("token_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.ARRAY(sa.String()), nullable=True),
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
    op.create_index(
        "ix_oauth_tokens_user_provider",
        "oauth_tokens",
        ["user_id", "provider"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_oauth_tokens_user_provider", table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
