"""Add tasks, tool_definitions, notifications tables and column additions.

Revision ID: 017
Revises: 016
Create Date: 2026-03-16
"""

import sqlalchemy as sa

from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- New tables ---

    # tasks
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("goal_id", sa.String(64), nullable=True),
        sa.Column("parent_task_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("task_type", sa.String(64), server_default="general", nullable=False),
        sa.Column("source", sa.String(32), server_default="user", nullable=False),
        sa.Column("priority", sa.String(16), server_default="medium", nullable=False),
        sa.Column("status", sa.String(32), server_default="created", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("assigned_agent", sa.String(32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("task_id"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.goal_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_task_id"], ["tasks.task_id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_user_status", "tasks", ["user_id", "status"])
    op.create_index("ix_tasks_goal", "tasks", ["goal_id"])
    op.create_index("ix_tasks_parent", "tasks", ["parent_task_id"])

    # task_dependencies
    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("depends_on_task_id", sa.String(64), nullable=False),
        sa.Column("dependency_type", sa.String(16), server_default="blocks", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_task_id"], ["tasks.task_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_task_deps_task", "task_dependencies", ["task_id"])
    op.create_index("ix_task_deps_depends_on", "task_dependencies", ["depends_on_task_id"])

    # tool_definitions
    op.create_table(
        "tool_definitions",
        sa.Column("tool_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), server_default="1.0", nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("input_schema", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("output_schema", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("risk_level", sa.String(16), server_default="low", nullable=False),
        sa.Column("requires_approval", sa.Boolean, server_default="false", nullable=False),
        sa.Column("timeout_seconds", sa.Integer, server_default="30", nullable=False),
        sa.Column("idempotent", sa.Boolean, server_default="false", nullable=False),
        sa.Column("connector_type", sa.String(32), nullable=True),
        sa.Column("enabled", sa.Boolean, server_default="true", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("tool_id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_tool_defs_connector", "tool_definitions", ["connector_type"])
    op.create_index("ix_tool_defs_risk", "tool_definitions", ["risk_level"])

    # notifications
    op.create_table(
        "notifications",
        sa.Column("notification_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), server_default="web", nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("payload_json", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("priority_score", sa.Float, server_default="0.5", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("notification_id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index(
        "ix_notifications_user_status", "notifications", ["user_id", "status", "created_at"]
    )
    op.create_index("ix_notifications_priority", "notifications", ["user_id", "priority_score"])

    # --- Column additions to existing tables ---

    # normalized_events
    op.add_column("normalized_events", sa.Column("correlation_id", sa.String(128), nullable=True))
    op.add_column("normalized_events", sa.Column("causation_id", sa.String(128), nullable=True))

    # users
    op.add_column("users", sa.Column("timezone", sa.String(64), nullable=True))

    # workspaces
    op.add_column("workspaces", sa.Column("slug", sa.String(128), nullable=True))
    op.add_column(
        "workspaces",
        sa.Column("type", sa.String(32), server_default="personal", nullable=False),
    )

    # sessions
    op.add_column("sessions", sa.Column("workspace_id", sa.String(64), nullable=True))

    # goals
    op.add_column(
        "goals",
        sa.Column("priority", sa.String(16), server_default="medium", nullable=False),
    )
    op.add_column(
        "goals", sa.Column("success_criteria_json", sa.dialects.postgresql.JSONB, nullable=True)
    )

    # entities
    op.add_column(
        "entities", sa.Column("confidence_score", sa.Float, server_default="1.0", nullable=False)
    )

    # triggers
    op.add_column(
        "triggers", sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "triggers", sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "triggers",
        sa.Column("source_config_json", sa.dialects.postgresql.JSONB, nullable=True),
    )

    # approvals
    op.add_column("approvals", sa.Column("step_id", sa.String(64), nullable=True))
    op.add_column("approvals", sa.Column("run_id", sa.String(64), nullable=True))
    op.add_column("approvals", sa.Column("requested_by", sa.String(64), nullable=True))
    op.add_column("approvals", sa.Column("approved_by", sa.String(64), nullable=True))

    # task_runs
    op.add_column("task_runs", sa.Column("task_id_ref", sa.String(64), nullable=True))
    op.add_column("task_runs", sa.Column("runtime_version", sa.String(32), nullable=True))
    op.add_column("task_runs", sa.Column("planner_version", sa.String(32), nullable=True))
    op.add_column("task_runs", sa.Column("verifier_version", sa.String(32), nullable=True))
    op.add_column(
        "task_runs", sa.Column("context_pack_json", sa.dialects.postgresql.JSONB, nullable=True)
    )

    # task_steps
    op.add_column("task_steps", sa.Column("step_order", sa.Integer, nullable=True))
    op.add_column("task_steps", sa.Column("step_type", sa.String(32), nullable=True))
    op.add_column("task_steps", sa.Column("name", sa.String(256), nullable=True))

    # plans
    op.add_column(
        "plans", sa.Column("success_conditions", sa.dialects.postgresql.JSONB, nullable=True)
    )

    # memories
    op.add_column("memories", sa.Column("superseded_by", sa.String(64), nullable=True))


def downgrade() -> None:
    # Drop new columns
    op.drop_column("memories", "superseded_by")
    op.drop_column("plans", "success_conditions")
    op.drop_column("task_steps", "name")
    op.drop_column("task_steps", "step_type")
    op.drop_column("task_steps", "step_order")
    op.drop_column("task_runs", "context_pack_json")
    op.drop_column("task_runs", "verifier_version")
    op.drop_column("task_runs", "planner_version")
    op.drop_column("task_runs", "runtime_version")
    op.drop_column("task_runs", "task_id_ref")
    op.drop_column("approvals", "approved_by")
    op.drop_column("approvals", "requested_by")
    op.drop_column("approvals", "run_id")
    op.drop_column("approvals", "step_id")
    op.drop_column("triggers", "source_config_json")
    op.drop_column("triggers", "last_evaluated_at")
    op.drop_column("triggers", "cooldown_until")
    op.drop_column("entities", "confidence_score")
    op.drop_column("goals", "success_criteria_json")
    op.drop_column("goals", "priority")
    op.drop_column("sessions", "workspace_id")
    op.drop_column("workspaces", "type")
    op.drop_column("workspaces", "slug")
    op.drop_column("users", "timezone")
    op.drop_column("normalized_events", "causation_id")
    op.drop_column("normalized_events", "correlation_id")

    # Drop new tables
    op.drop_index("ix_notifications_priority", table_name="notifications")
    op.drop_index("ix_notifications_user_status", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_tool_defs_risk", table_name="tool_definitions")
    op.drop_index("ix_tool_defs_connector", table_name="tool_definitions")
    op.drop_table("tool_definitions")
    op.drop_index("ix_task_deps_depends_on", table_name="task_dependencies")
    op.drop_index("ix_task_deps_task", table_name="task_dependencies")
    op.drop_table("task_dependencies")
    op.drop_index("ix_tasks_parent", table_name="tasks")
    op.drop_index("ix_tasks_goal", table_name="tasks")
    op.drop_index("ix_tasks_user_status", table_name="tasks")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")
