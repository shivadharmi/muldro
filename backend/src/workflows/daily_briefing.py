"""Daily Briefing Workflow.

Triggered by scheduler or manual request via the briefing endpoint.

Steps:
1. Fetch all important events since last briefing
2. Group by people, projects, tasks, deadlines
3. Retrieve relevant memories and preferences
4. Planner produces top priorities
5. Presenter generates text brief + structured payload
6. Store briefing snapshot
7. Notify user via Telegram / web dashboard
"""

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.services.presenter import Presenter

logger = logging.getLogger(__name__)


async def run_daily_briefing(
    user_id: str,
    settings: Settings,
    db: AsyncSession,
    briefing_date: date | None = None,
) -> str:
    """Generate and store the daily briefing. Returns briefing_id."""
    target_date = briefing_date or date.today()

    presenter = Presenter(settings=settings, db=db)
    briefing = await presenter.generate_briefing(user_id, target_date)

    logger.info("Daily briefing completed: %s for %s", briefing.briefing_id, target_date)
    return briefing.briefing_id
