"""Initial schema — all 14 tables.

Revision ID: 001
Revises:
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── normalized_events ────────────────────────────────────────
    op.create_table(
        "normalized_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_account_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("title", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("actor_entities", postgresql.JSONB),
        sa.Column("importance_signals", postgresql.JSONB),
        sa.Column("urgency_score", sa.Float),
        sa.Column("importance_score", sa.Float),
        sa.Column("confidence_score", sa.Float),
        sa.Column("raw_ref", sa.String(512)),
        sa.Column("idempotency_key", sa.String(256), unique=True, nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
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
    op.create_index("ix_events_user_occurred", "normalized_events", ["user_id", "occurred_at"])
    op.create_index(
        "ix_events_user_source_entity", "normalized_events", ["user_id", "source", "entity_id"]
    )

    # ── entities ─────────────────────────────────────────────────
    op.create_table(
        "entities",
        sa.Column("entity_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("canonical_name", sa.String(256), nullable=False),
        sa.Column("attributes", postgresql.JSONB),
        sa.Column("source_refs", postgresql.JSONB),
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
    op.create_index(
        "ix_entities_user_type_name", "entities", ["user_id", "entity_type", "canonical_name"]
    )

    # ── entity_aliases ───────────────────────────────────────────
    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "entity_id",
            sa.String(64),
            sa.ForeignKey("entities.entity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(256), nullable=False),
        sa.Column("alias_type", sa.String(32), server_default="name"),
    )
    op.create_index("ix_aliases_entity", "entity_aliases", ["entity_id"])
    op.create_index("ix_aliases_lookup", "entity_aliases", ["alias"])

    # ── entity_relationships ─────────────────────────────────────
    op.create_table(
        "entity_relationships",
        sa.Column("relation_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "from_entity_id",
            sa.String(64),
            sa.ForeignKey("entities.entity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column(
            "to_entity_id",
            sa.String(64),
            sa.ForeignKey("entities.entity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attributes", postgresql.JSONB),
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
    op.create_index(
        "ix_relations_from", "entity_relationships", ["from_entity_id", "relation_type"]
    )
    op.create_index("ix_relations_to", "entity_relationships", ["to_entity_id", "relation_type"])

    # ── memories ─────────────────────────────────────────────────
    op.create_table(
        "memories",
        sa.Column("memory_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("memory_type", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(64)),
        sa.Column("fact_text", sa.Text, nullable=False),
        sa.Column("embedding_ref", sa.String(128)),
        sa.Column("confidence", sa.Float, server_default="0.5"),
        sa.Column("stability_score", sa.Float, server_default="0.0"),
        sa.Column("source_event_ids", postgresql.JSONB),
        sa.Column("provenance", postgresql.JSONB),
        sa.Column("ttl_days", sa.Integer),
        sa.Column("status", sa.String(16), server_default="active"),
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
    op.create_index(
        "ix_memories_user_type_status", "memories", ["user_id", "memory_type", "status"]
    )

    # ── plans ────────────────────────────────────────────────────
    op.create_table(
        "plans",
        sa.Column("plan_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("trigger_ref", sa.String(128)),
        sa.Column("goal", sa.String(256), nullable=False),
        sa.Column("priority", sa.String(16), server_default="medium"),
        sa.Column("decision", sa.String(64), nullable=False),
        sa.Column("reasoning_summary", sa.Text),
        sa.Column("required_context", postgresql.JSONB),
        sa.Column("risk_level", sa.String(16), server_default="low"),
        sa.Column("execution_mode", sa.String(32), server_default="approval_required"),
        sa.Column("status", sa.String(32), server_default="created"),
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
    op.create_index("ix_plans_user_created", "plans", ["user_id", "created_at"])

    # ── plan_tasks ───────────────────────────────────────────────
    op.create_table(
        "plan_tasks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("plans.plan_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("input_data", postgresql.JSONB),
        sa.Column("depends_on", postgresql.JSONB),
        sa.Column("status", sa.String(32), server_default="pending"),
    )

    # ── executions ───────────────────────────────────────────────
    op.create_table(
        "executions",
        sa.Column("execution_id", sa.String(64), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(64),
            sa.ForeignKey("plans.plan_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("current_task_id", sa.String(64)),
        sa.Column("errors", postgresql.JSONB),
        sa.Column("audit_ref", sa.String(128)),
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

    # ── execution_task_runs ──────────────────────────────────────
    op.create_table(
        "execution_task_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "execution_id",
            sa.String(64),
            sa.ForeignKey("executions.execution_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("artifact_ref", sa.String(512)),
        sa.Column("result_data", postgresql.JSONB),
        sa.Column("error_message", sa.Text),
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

    # ── approvals ────────────────────────────────────────────────
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("approval_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("artifact_refs", postgresql.JSONB),
        sa.Column("risk_level", sa.String(16), server_default="medium"),
        sa.Column("status", sa.String(16), server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_reason", sa.Text),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
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
    op.create_index(
        "ix_approvals_user_status", "approvals", ["user_id", "status", "created_at"]
    )

    # ── briefings ────────────────────────────────────────────────
    op.create_table(
        "briefings",
        sa.Column("briefing_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("briefing_date", sa.Date, nullable=False),
        sa.Column("headline", sa.String(512)),
        sa.Column("top_priorities", postgresql.JSONB),
        sa.Column("changes_since_last", postgresql.JSONB),
        sa.Column("pending_approvals", postgresql.JSONB),
        sa.Column("recommended_actions", postgresql.JSONB),
        sa.Column("full_text", sa.Text),
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
    op.create_index("ix_briefings_user_date", "briefings", ["user_id", "briefing_date"])

    # ── audit_logs ───────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("event_id", sa.String(64)),
        sa.Column("plan_id", sa.String(64)),
        sa.Column("execution_id", sa.String(64)),
        sa.Column("approval_id", sa.String(64)),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("artifact_refs", postgresql.JSONB),
        sa.Column("policy_decision", sa.String(32)),
        sa.Column("details", postgresql.JSONB),
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

    # ── connectors ───────────────────────────────────────────────
    op.create_table(
        "connectors",
        sa.Column("connector_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="active"),
        sa.Column("config", postgresql.JSONB),
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

    # ── connector_accounts ───────────────────────────────────────
    op.create_table(
        "connector_accounts",
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("connector_id", sa.String(64), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("external_account_id", sa.String(256), nullable=False),
        sa.Column("credentials_encrypted", sa.Text),
        sa.Column("sync_cursor", sa.String(512)),
        sa.Column("sync_state", postgresql.JSONB),
        sa.Column("status", sa.String(16), server_default="active"),
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
    op.create_index(
        "ix_connector_accounts_connector", "connector_accounts", ["connector_id"]
    )


def downgrade() -> None:
    op.drop_table("connector_accounts")
    op.drop_table("connectors")
    op.drop_table("audit_logs")
    op.drop_table("briefings")
    op.drop_table("approvals")
    op.drop_table("execution_task_runs")
    op.drop_table("executions")
    op.drop_table("plan_tasks")
    op.drop_table("plans")
    op.drop_table("memories")
    op.drop_table("entity_relationships")
    op.drop_table("entity_aliases")
    op.drop_table("entities")
    op.drop_table("normalized_events")
