"""Add cache, thinking token columns to token_usage, traces, model_calls.

Revision ID: 025
Revises: 024
"""

from alembic import op
import sqlalchemy as sa

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # token_usage table
    op.add_column(
        "token_usage",
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "token_usage",
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "token_usage",
        sa.Column("thinking_tokens", sa.Integer(), nullable=False, server_default="0"),
    )

    # traces table
    op.add_column(
        "traces",
        sa.Column("total_cache_creation_tokens", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "traces",
        sa.Column("total_cache_read_tokens", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "traces",
        sa.Column("total_thinking_tokens", sa.Integer(), nullable=True, server_default="0"),
    )

    # model_calls table
    op.add_column(
        "model_calls",
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "model_calls",
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "model_calls",
        sa.Column("thinking_tokens", sa.Integer(), nullable=True, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("model_calls", "thinking_tokens")
    op.drop_column("model_calls", "cache_read_input_tokens")
    op.drop_column("model_calls", "cache_creation_input_tokens")
    op.drop_column("traces", "total_thinking_tokens")
    op.drop_column("traces", "total_cache_read_tokens")
    op.drop_column("traces", "total_cache_creation_tokens")
    op.drop_column("token_usage", "thinking_tokens")
    op.drop_column("token_usage", "cache_read_input_tokens")
    op.drop_column("token_usage", "cache_creation_input_tokens")
