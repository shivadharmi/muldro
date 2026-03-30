"""Schema integrity: FK constraints, type fixes, and missing indexes.

Adds missing foreign key constraints to approval_policies, webhook_subscriptions,
approvals, and artifacts. Adds missing indexes for common query patterns on
memories, task_runs, approvals, and entity_relationships.

Revision ID: 043
Revises: 042
Create Date: 2026-03-26
"""

from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- FK constraints ---

    # approval_policies.workspace_id → workspaces (was missing)
    op.create_foreign_key(
        "fk_apol_ws",
        "approval_policies",
        "workspaces",
        ["workspace_id"],
        ["workspace_id"],
        ondelete="CASCADE",
    )

    # webhook_subscriptions.workspace_id → workspaces (was missing)
    op.create_foreign_key(
        "fk_whsub_ws",
        "webhook_subscriptions",
        "workspaces",
        ["workspace_id"],
        ["workspace_id"],
        ondelete="CASCADE",
    )

    # approvals.run_id → task_runs (soft reference → proper FK)
    op.create_foreign_key(
        "fk_approvals_run",
        "approvals",
        "task_runs",
        ["run_id"],
        ["run_id"],
        ondelete="SET NULL",
    )

    # approvals.step_id → task_steps (soft reference → proper FK)
    op.create_foreign_key(
        "fk_approvals_step",
        "approvals",
        "task_steps",
        ["step_id"],
        ["step_id"],
        ondelete="SET NULL",
    )

    # artifacts.run_id → task_runs (soft reference → proper FK)
    op.create_foreign_key(
        "fk_artifacts_run",
        "artifacts",
        "task_runs",
        ["run_id"],
        ["run_id"],
        ondelete="SET NULL",
    )

    # --- Missing indexes ---

    op.create_index("ix_memories_user_scope", "memories", ["user_id", "scope"])
    op.create_index("ix_task_runs_ws_status", "task_runs", ["workspace_id", "status"])
    op.create_index("ix_approvals_run_status", "approvals", ["run_id", "status"])
    op.create_index("ix_entity_rels_ws", "entity_relationships", ["workspace_id"])


def downgrade() -> None:
    # --- Drop indexes (reverse order) ---
    op.drop_index("ix_entity_rels_ws", table_name="entity_relationships")
    op.drop_index("ix_approvals_run_status", table_name="approvals")
    op.drop_index("ix_task_runs_ws_status", table_name="task_runs")
    op.drop_index("ix_memories_user_scope", table_name="memories")

    # --- Drop FK constraints (reverse order) ---
    op.drop_constraint("fk_artifacts_run", "artifacts", type_="foreignkey")
    op.drop_constraint("fk_approvals_step", "approvals", type_="foreignkey")
    op.drop_constraint("fk_approvals_run", "approvals", type_="foreignkey")
    op.drop_constraint("fk_whsub_ws", "webhook_subscriptions", type_="foreignkey")
    op.drop_constraint("fk_apol_ws", "approval_policies", type_="foreignkey")
