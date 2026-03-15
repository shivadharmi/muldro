"""GoalTracker — tracks user goals and maps events to progress."""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.goals import Goal

logger = logging.getLogger(__name__)


class GoalTracker:
    """Tracks user goals and their progress."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def create_goal(
        self,
        user_id: str,
        title: str,
        description: str | None = None,
        target_date: datetime | None = None,
    ) -> str:
        """Create a new goal. Returns goal_id."""
        goal_id = f"goal_{ULID()}"
        goal = Goal(
            goal_id=goal_id,
            user_id=user_id,
            title=title,
            description=description,
            target_date=target_date,
            status="active",
            progress=0.0,
        )
        self._db.add(goal)
        await self._db.flush()
        logger.info("Created goal %s: %s", goal_id, title)
        return goal_id

    async def update_progress(self, goal_id: str, increment: float = 0.1) -> None:
        """Update goal progress by incrementing. Auto-completes at 1.0."""
        stmt = select(Goal).where(Goal.goal_id == goal_id)
        result = await self._db.execute(stmt)
        goal = result.scalar_one_or_none()

        if not goal:
            logger.warning("Goal %s not found for progress update", goal_id)
            return

        goal.progress = min(goal.progress + increment, 1.0)

        if goal.progress >= 1.0:
            goal.status = "completed"
            logger.info("Goal %s completed: %s", goal_id, goal.title)

        await self._db.flush()

    async def get_goal_status(self, user_id: str) -> list[dict]:
        """Get all active goals for a user."""
        stmt = (
            select(Goal)
            .where(Goal.user_id == user_id, Goal.status == "active")
            .order_by(Goal.created_at.desc())
        )

        result = await self._db.execute(stmt)
        goals = result.scalars().all()

        return [
            {
                "goal_id": g.goal_id,
                "title": g.title,
                "description": g.description,
                "target_date": g.target_date.isoformat() if g.target_date else None,
                "progress": g.progress,
                "status": g.status,
            }
            for g in goals
        ]

    async def detect_goal_relevance(
        self, user_id: str, event_title: str, event_summary: str
    ) -> list[dict]:
        """Simple keyword match against goal titles/descriptions to find relevant goals."""
        stmt = select(Goal).where(Goal.user_id == user_id, Goal.status == "active")

        result = await self._db.execute(stmt)
        goals = result.scalars().all()

        relevant_goals = []
        search_text = f"{event_title} {event_summary}".lower()

        for goal in goals:
            # Check if goal title or description keywords appear in event
            goal_text = f"{goal.title} {goal.description or ''}".lower()
            keywords = set(goal_text.split())

            # Simple overlap check: if >2 keywords match, consider relevant
            matches = sum(1 for word in keywords if len(word) > 3 and word in search_text)

            if matches >= 2:
                relevant_goals.append(
                    {
                        "goal_id": goal.goal_id,
                        "title": goal.title,
                        "progress": goal.progress,
                        "match_score": matches,
                    }
                )

        return sorted(relevant_goals, key=lambda x: x["match_score"], reverse=True)
