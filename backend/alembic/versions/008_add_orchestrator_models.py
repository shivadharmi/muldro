"""Add orchestrator models: conversations, messages, token_usage, agent_decision_logs, observation_cursors.

Revision ID: 008
Revises: 007
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Conversations
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("surface", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="active"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_user_status", "conversations", ["user_id", "status"])

    # Messages
    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(64), primary_key=True),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("surface", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])

    # Token Usage
    op.create_table(
        "token_usage",
        sa.Column("usage_id", sa.String(64), primary_key=True),
        sa.Column("agent_name", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trigger", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_token_usage_agent_created", "token_usage", ["agent_name", "created_at"])
    op.create_index("ix_token_usage_trace", "token_usage", ["trace_id"])

    # Agent Decision Logs
    op.create_table(
        "agent_decision_logs",
        sa.Column("log_id", sa.String(64), primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("span_id", sa.String(64), nullable=True),
        sa.Column("agent_name", sa.String(32), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(64), nullable=True),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("latency_ms", sa.Integer(), server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_decision_logs_trace_id", "agent_decision_logs", ["trace_id"])
    op.create_index(
        "ix_agent_decision_log_agent_created",
        "agent_decision_logs",
        ["agent_name", "created_at"],
    )

    # Observation Cursors
    op.create_table(
        "observation_cursors",
        sa.Column("cursor_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("cursor_type", sa.String(32), nullable=False),
        sa.Column("cursor_value", sa.String(512), nullable=False),
        sa.Column("last_observation_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_cursor_user_source", "observation_cursors", ["user_id", "source"])
    op.create_index("ix_cursor_user_source", "observation_cursors", ["user_id", "source"])


def downgrade() -> None:
    op.drop_table("observation_cursors")
    op.drop_table("agent_decision_logs")
    op.drop_table("token_usage")
    op.drop_table("messages")
    op.drop_table("conversations")
