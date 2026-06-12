"""Schedule seeder — creates default proactive schedules on first startup.

Connector-independent schedules (WORKSPACE_CREATION_SCHEDULES: morning_briefing,
memory_consolidation, slo_health_check) are seeded **enabled** at workspace
creation so the proactive/briefing loop is reachable before any OAuth.
Connector-dependent observe_* schedules are seeded **disabled** and enabled when
the matching connector is authorized via OAuth (see routes_auth.py), because each
polls a specific source and provisions per-provider PerceptionState.

Mapping (connector → schedules enabled on authorization):
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

# Schedules enabled immediately at workspace creation — the connector-independent
# proactive/housekeeping ones. This makes the daily briefing and the proactive
# loop reachable for a brand-new user *before* they connect any OAuth source
# (the audit's "proactive loop that never fires" gap). The dashboard renders a
# briefing surface with a "gathering data" empty state until the first run.
# observe_* schedules are deliberately excluded: each polls a specific connector
# and provisions per-provider PerceptionState, so they stay gated on OAuth.
WORKSPACE_CREATION_SCHEDULES = GLOBAL_SCHEDULES

# Per-connector schedule mapping.
CONNECTOR_SCHEDULES: dict[str, list[str]] = {
    "google": ["observe_gmail", "observe_calendar"],
    "gmail": ["observe_gmail"],
    "calendar": ["observe_calendar"],
    "slack": ["observe_slack"],
    "github": ["observe_github"],
}


async def seed_default_schedules(db: AsyncSession, user_id: str, workspace_id: str = "") -> int:
    """Seed or update default schedules. Returns count created/updated.

    Creates new schedules as disabled. For existing schedules, syncs
    cron_expr, action_type, action_config, and priority from defaults
    so code changes propagate on restart. Does NOT change the enabled flag
    (that is controlled by connector authorization).
    """
    result = await db.execute(
        select(Schedule).where(
            Schedule.source == "system",
            Schedule.workspace_id == workspace_id,
        )
    )
    existing = {s.name: s for s in result.scalars().all()}

    now = datetime.now(timezone.utc)
    changed = 0

    for sched_def in DEFAULT_SCHEDULES:
        name = sched_def["name"]
        cron_expr = sched_def.get("cron_expr")

        if name not in existing:
            next_run = None
            if cron_expr:
                next_run = croniter(cron_expr, now).get_next(datetime)

            schedule = Schedule(
                schedule_id=f"sched_{ULID()}",
                user_id=user_id,
                workspace_id=workspace_id,
                name=name,
                description=sched_def.get("description"),
                schedule_type=sched_def["schedule_type"],
                cron_expr=cron_expr,
                action_type=sched_def["action_type"],
                action_config=sched_def.get("action_config"),
                enabled=name in WORKSPACE_CREATION_SCHEDULES,
                source=sched_def.get("source", "system"),
                priority=sched_def.get("priority", "medium"),
                next_run_at=next_run,
            )
            db.add(schedule)
            changed += 1
            continue

        # Sync mutable fields (never touch enabled — user/connector controls that)
        sched = existing[name]
        needs_update = False

        if sched.cron_expr != cron_expr:
            sched.cron_expr = cron_expr
            needs_update = True
        if sched.action_type != sched_def["action_type"]:
            sched.action_type = sched_def["action_type"]
            needs_update = True
        if sched.action_config != sched_def.get("action_config"):
            sched.action_config = sched_def.get("action_config")
            needs_update = True
        if sched.priority != sched_def.get("priority", "medium"):
            sched.priority = sched_def.get("priority", "medium")
            needs_update = True

        if needs_update:
            changed += 1

    if changed:
        await db.flush()
        logger.info("Seeded/updated %d default schedules", changed)

    return changed


async def enable_schedules_for_connector(
    db: AsyncSession,
    provider: str,
    workspace_id: str = "",
) -> list[str]:
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
            Schedule.workspace_id == workspace_id,
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
            Schedule.workspace_id == workspace_id,
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

    # Upsert PerceptionState for the connector source
    try:
        from src.services.perception_policy import DEFAULT_INTERVALS, PerceptionPolicyService

        # Get user/workspace from one of the enabled schedules
        observe_name = f"observe_{provider}"
        sched_row = await db.execute(
            select(Schedule)
            .where(
                Schedule.name == observe_name,
                Schedule.workspace_id == workspace_id,
            )
            .limit(1)
        )
        sched_obj = sched_row.scalar_one_or_none()
        if sched_obj:
            policy_svc = PerceptionPolicyService(db)
            state = await policy_svc.get_or_create_state(
                sched_obj.workspace_id, sched_obj.user_id, provider
            )
            state.mode = "poll"
            state.base_interval_s = DEFAULT_INTERVALS.get(provider, 300)
            state.effective_interval_s = state.base_interval_s
            state.next_run_at = now
            state.circuit_state = "closed"
            await db.flush()
    except Exception:
        logger.debug("Failed to upsert perception state for %s", provider, exc_info=True)

    return enabled
