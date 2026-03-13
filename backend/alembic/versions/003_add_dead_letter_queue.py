"""Add dead-letter queue table.

Revision ID: 003
Revises: 002
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dead_letter_queue",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("entry_id", sa.String(64), unique=True, nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("operation_type", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128)),
        sa.Column("error_type", sa.String(128), nullable=False),
        sa.Column("error_message", sa.Text),
        sa.Column("error_context", postgresql.JSONB),
        sa.Column("attempt_count", sa.Integer, server_default="1"),
        sa.Column("max_attempts", sa.Integer, server_default="3"),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("payload", postgresql.JSONB),
        sa.Column(
            "last_attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
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
    op.create_index("ix_dlq_user_status", "dead_letter_queue", ["user_id", "status"])
    op.create_index(
        "ix_dlq_operation_status", "dead_letter_queue", ["operation_type", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_dlq_operation_status", table_name="dead_letter_queue")
    op.drop_index("ix_dlq_user_status", table_name="dead_letter_queue")
    op.drop_table("dead_letter_queue")
