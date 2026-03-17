"""Schedule seeder — creates default proactive schedules on first startup.

Seeds:
- Morning briefing (7:00 AM daily)
- Perception cycles for each connector (gmail, calendar, slack, github)
- Memory consolidation (nightly at 2:00 AM)
- SLO health check (every 6 hours)
"""

import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.schedules import Schedule

logger = logging.getLogger(__name__)

DEFAULT_SCHEDULES: list[dict] = [
    {
        "name": "morning_briefing",
        "description": "Generate daily morning briefing at 7:00 AM.",
        "schedule_type": "recurring",
        "cron_expr": "0 7 * * *",
        "action_type": "generate_briefing",
        "action_config": {},
        "source": "system",
        "priority": "high",
    },
    {
        "name": "observe_gmail",
        "description": "Poll Gmail for new emails every 5 minutes.",
        "schedule_type": "recurring",
        "cron_expr": "*/5 * * * *",
        "action_type": "observe_source",
        "action_config": {"source": "gmail"},
        "source": "system",
        "priority": "medium",
    },
    {
        "name": "observe_calendar",
        "description": "Poll calendar for upcoming events every 15 minutes.",
        "schedule_type": "recurring",
        "cron_expr": "*/15 * * * *",
        "action_type": "observe_source",
        "action_config": {"source": "calendar"},
        "source": "system",
        "priority": "medium",
    },
    {
        "name": "observe_slack",
        "description": "Poll Slack for new messages every 5 minutes.",
        "schedule_type": "recurring",
        "cron_expr": "*/5 * * * *",
        "action_type": "observe_source",
        "action_config": {"source": "slack"},
        "source": "system",
        "priority": "medium",
    },
    {
        "name": "observe_github",
        "description": "Poll GitHub for activity every 10 minutes.",
        "schedule_type": "recurring",
        "cron_expr": "*/10 * * * *",
        "action_type": "observe_source",
        "action_config": {"source": "github"},
        "source": "system",
        "priority": "low",
    },
    {
        "name": "memory_consolidation",
        "description": "Consolidate and merge related memories nightly.",
        "schedule_type": "recurring",
        "cron_expr": "0 2 * * *",
        "action_type": "consolidate_memories",
        "action_config": {},
        "source": "system",
        "priority": "low",
    },
    {
        "name": "slo_health_check",
        "description": "Check SLO health every 6 hours.",
        "schedule_type": "recurring",
        "cron_expr": "0 */6 * * *",
        "action_type": "check_slos",
        "action_config": {},
        "source": "system",
        "priority": "medium",
    },
]


async def seed_default_schedules(db: AsyncSession, user_id: str, workspace_id: str = "") -> int:
    """Seed default schedules if they don't already exist. Returns count seeded."""
    result = await db.execute(select(Schedule.name))
    existing = {row[0] for row in result.all()}

    now = datetime.now(timezone.utc)
    seeded = 0

    for sched_def in DEFAULT_SCHEDULES:
        if sched_def["name"] in existing:
            continue

        cron_expr = sched_def.get("cron_expr")
        next_run = None
        if cron_expr:
            next_run = croniter(cron_expr, now).get_next(datetime)

        schedule = Schedule(
            schedule_id=f"sched_{ULID()}",
            user_id=user_id,
            workspace_id=workspace_id,
            name=sched_def["name"],
            description=sched_def.get("description"),
            schedule_type=sched_def["schedule_type"],
            cron_expr=cron_expr,
            action_type=sched_def["action_type"],
            action_config=sched_def.get("action_config"),
            enabled=True,
            source=sched_def.get("source", "system"),
            priority=sched_def.get("priority", "medium"),
            next_run_at=next_run,
        )
        db.add(schedule)
        seeded += 1

    if seeded:
        await db.flush()
        logger.info("Seeded %d default schedules", seeded)

    return seeded
