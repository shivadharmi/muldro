"""Add browser automation tables — sessions and actions.

Revision ID: 016
Revises: 015
Create Date: 2026-03-16
"""

import sqlalchemy as sa

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # browser_sessions table
    op.create_table(
        "browser_sessions",
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="idle"),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("page_title", sa.String(500), nullable=True),
        sa.Column("screenshot_artifact_id", sa.String(64), nullable=True),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_browser_sessions_user_id", "browser_sessions", ["user_id"])
    op.create_index(
        "ix_browser_sessions_user_status", "browser_sessions", ["user_id", "status"]
    )

    # browser_actions table
    op.create_table(
        "browser_actions",
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("selector", sa.Text, nullable=True),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("result_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("screenshot_before", sa.String(64), nullable=True),
        sa.Column("screenshot_after", sa.String(64), nullable=True),
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
        sa.PrimaryKeyConstraint("action_id"),
    )
    op.create_index("ix_browser_actions_session_id", "browser_actions", ["session_id"])
    op.create_index(
        "ix_browser_actions_session", "browser_actions", ["session_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_browser_actions_session", table_name="browser_actions")
    op.drop_index("ix_browser_actions_session_id", table_name="browser_actions")
    op.drop_table("browser_actions")
    op.drop_index("ix_browser_sessions_user_status", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_user_id", table_name="browser_sessions")
    op.drop_table("browser_sessions")
