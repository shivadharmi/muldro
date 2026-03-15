"""Add working_memory, artifacts, procedures tables.

Revision ID: 013
Revises: 012
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Working memory
    op.create_table(
        "working_memory",
        sa.Column("entry_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("entry_type", sa.String(32), nullable=False, server_default="variable"),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", JSONB, nullable=True),
        sa.Column("ttl_seconds", sa.Integer, nullable=False, server_default="3600"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_wm_user_session", "working_memory", ["user_id", "session_id"])
    op.create_index("ix_wm_user_key", "working_memory", ["user_id", "key"])
    op.create_index("ix_wm_expires", "working_memory", ["expires_at"])

    # Artifacts
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("s3_key", sa.String(512), nullable=False),
        sa.Column("s3_bucket", sa.String(128), nullable=False),
        sa.Column("source_ref", JSONB, nullable=True),
        sa.Column("entity_links", ARRAY(sa.String(64)), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
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
    op.create_index("ix_artifacts_user_type", "artifacts", ["user_id", "artifact_type"])

    # Procedures
    op.create_table(
        "procedures",
        sa.Column("procedure_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("trigger_pattern", JSONB, nullable=True),
        sa.Column("task_template", JSONB, nullable=True),
        sa.Column("learned_from", ARRAY(sa.String(64)), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("usage_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
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
    op.create_index("ix_procedures_user_status", "procedures", ["user_id", "status"])


def downgrade() -> None:
    op.drop_table("procedures")
    op.drop_table("artifacts")
    op.drop_table("working_memory")
