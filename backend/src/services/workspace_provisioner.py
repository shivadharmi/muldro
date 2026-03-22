"""Workspace provisioner — seeds defaults when a user signs up.

Called once when a new user + workspace are created (during onboarding).
Seeds: default schedules, trust records, connector installations.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def provision_workspace(db: AsyncSession, user_id: str, workspace_id: str) -> dict[str, int]:
    """Seed all defaults for a newly created workspace.

    Returns a dict of {resource_type: count_seeded}.
    """
    counts: dict[str, int] = {}

    # 1. Default schedules
    from src.services.schedule_seeder import seed_default_schedules

    counts["schedules"] = await seed_default_schedules(
        db, user_id=user_id, workspace_id=workspace_id
    )

    # 2. Trust records (T0/T1 for built-in servers)
    from src.integrations.seeds import seed_trust_records

    trust_records = await seed_trust_records(db, workspace_id)
    counts["trust_records"] = len(trust_records)

    # 3. Default connector installations
    from src.integrations.seed_installations import seed_installations

    counts["installations"] = await seed_installations(db, workspace_id, user_id)

    total = sum(counts.values())
    if total:
        logger.info(
            "Provisioned workspace %s for %s: %s",
            workspace_id,
            user_id,
            counts,
        )

    return counts
