"""Add traces and model_calls tables for persistent observability.

Revision ID: 022
Revises: 021
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "traces",
        sa.Column("trace_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("total_input_tokens", sa.Integer, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer, server_default="0"),
        sa.Column("total_cost_usd", sa.Float, server_default="0.0"),
        sa.Column("span_count", sa.Integer, server_default="0"),
        sa.Column("error_count", sa.Integer, server_default="0"),
        sa.Column("agents_invoked", postgresql.ARRAY(sa.String(32))),
        sa.Column("tools_called", postgresql.ARRAY(sa.String(128))),
        sa.Column("context_summary", sa.String(2000)),
        sa.Column("final_result", sa.String(2000)),
        sa.Column("memory_writes", sa.Integer, server_default="0"),
        sa.Column("approval_ids", postgresql.ARRAY(sa.String(64))),
        sa.Column("spans_json", postgresql.JSONB),
        sa.Column("metadata_json", postgresql.JSONB),
    )
    op.create_index("ix_traces_user_started", "traces", ["user_id", "started_at"])
    op.create_index("ix_traces_trigger", "traces", ["trigger"])
    op.create_index("ix_traces_status", "traces", ["status"])

    op.create_table(
        "model_calls",
        sa.Column("call_id", sa.String(64), primary_key=True),
        sa.Column(
            "trace_id",
            sa.String(64),
            sa.ForeignKey("traces.trace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer, server_default="0"),
        sa.Column("output_tokens", sa.Integer, server_default="0"),
        sa.Column("cost_usd", sa.Float, server_default="0.0"),
        sa.Column("duration_ms", sa.Integer, server_default="0"),
        sa.Column("tools_called", postgresql.ARRAY(sa.String(128))),
        sa.Column("decision", sa.String(256)),
        sa.Column("error", sa.String(1000)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_model_calls_trace", "model_calls", ["trace_id"])
    op.create_index(
        "ix_model_calls_agent_created", "model_calls", ["agent_name", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("model_calls")
    op.drop_table("traces")
