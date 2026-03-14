"""Add schedules table for backend-owned dynamic scheduling.

Revision ID: 006
Revises: 005
Create Date: 2026-03-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

# Default system schedules seeded on first migration
SEED_SCHEDULES = [
    {
        "schedule_id": "sched_system_observe_gmail",
        "user_id": "usr_default",
        "name": "observe-gmail",
        "description": "Check Gmail for new emails every 15 minutes",
        "schedule_type": "recurring",
        "cron_expr": "*/15 * * * *",
        "action_type": "observe_source",
        "action_config": {"source": "gmail"},
        "enabled": False,
        "run_count": 0,
        "consecutive_failures": 0,
        "source": "system",
        "priority": "medium",
    },
    {
        "schedule_id": "sched_system_observe_calendar",
        "user_id": "usr_default",
        "name": "observe-calendar",
        "description": "Check Google Calendar every 2 hours",
        "schedule_type": "recurring",
        "cron_expr": "0 */2 * * *",
        "action_type": "observe_source",
        "action_config": {"source": "calendar"},
        "enabled": False,
        "run_count": 0,
        "consecutive_failures": 0,
        "source": "system",
        "priority": "medium",
    },
    {
        "schedule_id": "sched_system_observe_github",
        "user_id": "usr_default",
        "name": "observe-github",
        "description": "Check GitHub for PRs, issues, notifications every 30 minutes",
        "schedule_type": "recurring",
        "cron_expr": "*/30 * * * *",
        "action_type": "observe_source",
        "action_config": {"source": "github"},
        "enabled": False,
        "run_count": 0,
        "consecutive_failures": 0,
        "source": "system",
        "priority": "medium",
    },
    {
        "schedule_id": "sched_system_morning_briefing",
        "user_id": "usr_default",
        "name": "morning-briefing",
        "description": "Generate and deliver daily briefing at 7 AM weekdays",
        "schedule_type": "recurring",
        "cron_expr": "0 7 * * 1-5",
        "action_type": "generate_briefing",
        "action_config": {},
        "enabled": False,
        "run_count": 0,
        "consecutive_failures": 0,
        "source": "system",
        "priority": "high",
    },
    {
        "schedule_id": "sched_system_meeting_prep",
        "user_id": "usr_default",
        "name": "meeting-prep",
        "description": "Check for upcoming meetings and prepare cards, weekdays 8-18",
        "schedule_type": "recurring",
        "cron_expr": "*/30 8-18 * * 1-5",
        "action_type": "meeting_prep",
        "action_config": {},
        "enabled": False,
        "run_count": 0,
        "consecutive_failures": 0,
        "source": "system",
        "priority": "medium",
    },
    {
        "schedule_id": "sched_system_heartbeat",
        "user_id": "usr_default",
        "name": "heartbeat",
        "description": "Run system maintenance every hour",
        "schedule_type": "recurring",
        "cron_expr": "0 * * * *",
        "action_type": "heartbeat",
        "action_config": {},
        "enabled": True,
        "run_count": 0,
        "consecutive_failures": 0,
        "source": "system",
        "priority": "low",
    },
]


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("schedule_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("schedule_type", sa.String(32), nullable=False),
        sa.Column("cron_expr", sa.String(128), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("action_config", JSONB, nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False, server_default=sa.text("'medium'")),
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
        "ix_sched_user_next", "schedules", ["user_id", "enabled", "next_run_at"]
    )

    # Seed default system schedules with pre-computed next_run_at
    from datetime import datetime, timezone

    from croniter import croniter

    now = datetime.now(timezone.utc)
    table = sa.table(
        "schedules",
        sa.column("schedule_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("schedule_type", sa.String),
        sa.column("cron_expr", sa.String),
        sa.column("action_type", sa.String),
        sa.column("action_config", JSONB),
        sa.column("enabled", sa.Boolean),
        sa.column("next_run_at", sa.DateTime(timezone=True)),
        sa.column("run_count", sa.Integer),
        sa.column("consecutive_failures", sa.Integer),
        sa.column("source", sa.String),
        sa.column("priority", sa.String),
    )
    for seed in SEED_SCHEDULES:
        # Only compute next_run_at for enabled schedules
        next_run = croniter(seed["cron_expr"], now).get_next(datetime) if seed["enabled"] else None
        op.bulk_insert(
            table,
            [
                {
                    "schedule_id": seed["schedule_id"],
                    "user_id": seed["user_id"],
                    "name": seed["name"],
                    "description": seed["description"],
                    "schedule_type": seed["schedule_type"],
                    "cron_expr": seed["cron_expr"],
                    "action_type": seed["action_type"],
                    "action_config": seed["action_config"],
                    "enabled": seed["enabled"],
                    "next_run_at": next_run,
                    "run_count": seed["run_count"],
                    "consecutive_failures": seed["consecutive_failures"],
                    "source": seed["source"],
                    "priority": seed["priority"],
                },
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_sched_user_next", table_name="schedules")
    op.drop_table("schedules")
