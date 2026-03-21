"""Schedule seeder — creates default proactive schedules on first startup.

All schedules are seeded as **disabled**. They are enabled when the
corresponding connector is authorized via OAuth (see routes_auth.py).

Mapping (connector → schedules enabled):
  Any connector  → morning_briefing, memory_consolidation, slo_health_check
  gmail          → observe_gmail
  calendar       → observe_calendar
  slack          → observe_slack
  github         → observe_github
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

# Schedules enabled when the *first* connector of any kind is authorized.
GLOBAL_SCHEDULES = {"morning_briefing", "memory_consolidation", "slo_health_check"}

# Per-connector schedule mapping.
CONNECTOR_SCHEDULES: dict[str, list[str]] = {
    "gmail": ["observe_gmail"],
    "calendar": ["observe_calendar"],
    "slack": ["observe_slack"],
    "github": ["observe_github"],
}


async def seed_default_schedules(db: AsyncSession, user_id: str, workspace_id: str = "") -> int:
    """Seed default schedules as disabled. Returns count seeded."""
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
            enabled=False,
            source=sched_def.get("source", "system"),
            priority=sched_def.get("priority", "medium"),
            next_run_at=next_run,
        )
        db.add(schedule)
        seeded += 1

    if seeded:
        await db.flush()
        logger.info("Seeded %d default schedules (all disabled)", seeded)

    return seeded


async def enable_schedules_for_connector(db: AsyncSession, provider: str) -> list[str]:
    """Enable schedules associated with a newly-authorized connector.

    Returns the list of schedule names that were enabled.
    """
    now = datetime.now(timezone.utc)
    names_to_enable: set[str] = set()

    # 1. Always enable the connector-specific schedule
    for name in CONNECTOR_SCHEDULES.get(provider, []):
        names_to_enable.add(name)

    # 2. Enable global schedules (briefing, consolidation, SLO) on first connector
    #    Check if any observe_* schedule is already enabled — if so, globals are already on.
    result = await db.execute(
        select(Schedule.name).where(
            Schedule.name.like("observe_%"),
            Schedule.enabled.is_(True),
        )
    )
    has_existing_connector = bool(result.first())
    if not has_existing_connector:
        names_to_enable.update(GLOBAL_SCHEDULES)

    if not names_to_enable:
        return []

    # Fetch and enable
    result = await db.execute(
        select(Schedule).where(
            Schedule.name.in_(names_to_enable),
            Schedule.enabled.is_(False),
        )
    )
    enabled: list[str] = []
    for sched in result.scalars().all():
        sched.enabled = True
        if not sched.next_run_at and sched.cron_expr:
            sched.next_run_at = croniter(sched.cron_expr, now).get_next(datetime)
        enabled.append(sched.name)

    if enabled:
        await db.flush()
        logger.info("Enabled schedules for connector %s: %s", provider, enabled)

    return enabled
