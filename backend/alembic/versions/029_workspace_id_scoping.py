"""Add workspace_id to all shared data tables for multi-tenant scoping.

Tables affected:
- entities, entity_aliases, entity_relationships
- memories
- goals
- plans, plan_tasks
- task_runs, task_steps, task_checkpoints
- artifacts
- triggers
- approvals
- browser_sessions, browser_actions
- traces, model_calls

NOT affected (user-level, auth, or system-global):
- users, workspaces, workspace_members, magic_links, sessions
- user_settings
- agents, agent_routes

Revision ID: 029
Revises: 028
Create Date: 2026-03-18
"""

import sqlalchemy as sa
from alembic import op

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None

# All tables that get workspace_id
TABLES = [
    "entities",
    "entity_aliases",
    "entity_relationships",
    "memories",
    "goals",
    "plans",
    "plan_tasks",
    "task_runs",
    "task_steps",
    "task_checkpoints",
    "artifacts",
    "triggers",
    "approvals",
    "browser_sessions",
    "browser_actions",
    "traces",
    "model_calls",
    "normalized_events",
    "notifications",
    "conversations",
    "messages",
    "schedules",
    "connectors",
    "connector_accounts",
    "tasks",
    "task_dependencies",
    "token_usage",
    "trust_scores",
    "observation_cursors",
    "observation_status",
    "briefings",
    "briefing_feedback",
    "audit_logs",
    "dead_letter_queue",
    "procedures",
    "tool_definitions",
    "working_memory",
    "ui_surfaces",
    "agent_decision_logs",
    "oauth_connections",
    "oauth_tokens",
]


def upgrade() -> None:
    conn = op.get_bind()

    # Find the default workspace to backfill existing rows
    result = conn.execute(sa.text("SELECT workspace_id FROM workspaces LIMIT 1"))
    row = result.fetchone()
    default_ws = row[0] if row else "ws_default"

    for table in TABLES:
        # 1. Add column as nullable first
        op.add_column(
            table,
            sa.Column(
                "workspace_id",
                sa.String(64),
                sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        # 2. Backfill existing rows with default workspace
        conn.execute(
            sa.text(f"UPDATE {table} SET workspace_id = :ws WHERE workspace_id IS NULL"),
            {"ws": default_ws},
        )
        # 3. Set NOT NULL constraint
        op.alter_column(table, "workspace_id", nullable=False)
        # 4. Create index
        op.create_index(f"ix_{table}_workspace", table, ["workspace_id"])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_index(f"ix_{table}_workspace", table_name=table)
        op.drop_column(table, "workspace_id")
