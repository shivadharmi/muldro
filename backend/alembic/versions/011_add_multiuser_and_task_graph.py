"""Add multi-user auth tables and task graph execution engine.

Revision ID: 011
Revises: 010
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Users ────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(256), unique=True, nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("onboarding_completed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("settings", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # ── Workspaces ───────────────────────────────────────────
    op.create_table(
        "workspaces",
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.String(64),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("plan", sa.String(16), server_default="free", nullable=False),
        sa.Column("settings", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False
        ),
        sa.Column("role", sa.String(32), server_default="owner", nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workspace_members_unique",
        "workspace_members",
        ["workspace_id", "user_id"],
        unique=True,
    )

    # ── Magic Links ──────────────────────────────────────────
    op.create_table(
        "magic_links",
        sa.Column("link_id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("token_hash", sa.String(256), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_magic_links_email", "magic_links", ["email"])

    # ── Sessions ─────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False
        ),
        sa.Column("token_hash", sa.String(256), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("surface", sa.String(32), server_default="web", nullable=False),
        sa.Column("device_info", JSONB(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_sessions_user_created", "sessions", ["user_id", "created_at"])

    # ── OAuth Connections ────────────────────────────────────
    op.create_table(
        "oauth_connections",
        sa.Column("connection_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_user_id", sa.String(128), nullable=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_oauth_connections_user_provider",
        "oauth_connections",
        ["user_id", "provider"],
        unique=True,
    )

    # ── User Settings ────────────────────────────────────────
    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False
        ),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_user_settings_unique",
        "user_settings",
        ["user_id", "category", "key"],
        unique=True,
    )

    # ── Task Runs (Graph Executor) ───────────────────────────
    op.create_table(
        "task_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("plans.plan_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("graph_definition", JSONB(), nullable=True),
        sa.Column("current_step_ids", ARRAY(sa.String(64)), nullable=True),
        sa.Column("checkpoint", JSONB(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default="3", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_task_runs_user_status", "task_runs", ["user_id", "status", "created_at"])

    # ── Task Steps ───────────────────────────────────────────
    op.create_table(
        "task_steps",
        sa.Column("step_id", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("task_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("depends_on", ARRAY(sa.String(64)), nullable=True),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("input_data", JSONB(), nullable=True),
        sa.Column("output_data", JSONB(), nullable=True),
        sa.Column("artifact_refs", ARRAY(sa.String(512)), nullable=True),
        sa.Column("error", JSONB(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default="3", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_task_steps_run_status", "task_steps", ["run_id", "status"])

    # ── Task Checkpoints ─────────────────────────────────────
    op.create_table(
        "task_checkpoints",
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("task_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(64), nullable=True),
        sa.Column("state_snapshot", JSONB(), nullable=True),
        sa.Column("reason", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_task_checkpoints_run", "task_checkpoints", ["run_id", "created_at"])

    # Users are created at runtime via AuthService (magic link or OAuth flow).
    # No seed data — use scripts/create_test_user.py for testing.


def downgrade() -> None:
    op.drop_table("task_checkpoints")
    op.drop_table("task_steps")
    op.drop_table("task_runs")
    op.drop_table("user_settings")
    op.drop_table("oauth_connections")
    op.drop_table("sessions")
    op.drop_table("magic_links")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("users")
