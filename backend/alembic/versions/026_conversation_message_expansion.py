"""Expand conversation and message models with proper columns.

Add title, message_count, token/cost aggregates to conversations.
Add trace_id, input_tokens, output_tokens, cost_usd to messages.
Add FK from messages.conversation_id -> conversations.conversation_id.

Revision ID: 026
Revises: 025
Create Date: 2026-03-18
"""

import sqlalchemy as sa
from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Conversations: add new columns --
    op.add_column("conversations", sa.Column("title", sa.String(256), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("total_input_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("total_output_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("total_cost_usd", sa.Numeric(10, 6), server_default="0", nullable=False),
    )

    # -- Messages: add new columns --
    op.add_column("messages", sa.Column("trace_id", sa.String(64), nullable=True))
    op.add_column("messages", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True))

    # -- Add FK (messages -> conversations) --
    op.create_foreign_key(
        "fk_messages_conversation_id",
        "messages",
        "conversations",
        ["conversation_id"],
        ["conversation_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_messages_conversation_id", "messages", type_="foreignkey")
    op.drop_column("messages", "cost_usd")
    op.drop_column("messages", "output_tokens")
    op.drop_column("messages", "input_tokens")
    op.drop_column("messages", "trace_id")
    op.drop_column("conversations", "total_cost_usd")
    op.drop_column("conversations", "total_output_tokens")
    op.drop_column("conversations", "total_input_tokens")
    op.drop_column("conversations", "message_count")
    op.drop_column("conversations", "title")
