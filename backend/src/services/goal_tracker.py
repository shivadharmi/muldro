"""Goal tracking service — tracks user goals and maps events to progress."""

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
        related_entity_ids: list[str] | None = None,
    ) -> Goal:
        """Create a new goal."""
        goal = Goal(
            goal_id=f"goal_{ULID()}",
            user_id=user_id,
            title=title,
            description=description,
            target_date=target_date,
            status="active",
            progress=0.0,
            related_entity_ids=related_entity_ids,
        )
        self._db.add(goal)
        await self._db.commit()
        logger.info("Goal created: %s '%s' for user %s", goal.goal_id, title, user_id)
        return goal

    async def get_active_goals(self, user_id: str) -> list[dict]:
        """Get all active goals for a user."""
        result = await self._db.execute(
            select(Goal)
            .where(Goal.user_id == user_id, Goal.status == "active")
            .order_by(Goal.created_at.desc())
        )
        return [
            {
                "goal_id": g.goal_id,
                "title": g.title,
                "description": g.description,
                "target_date": g.target_date.isoformat() if g.target_date else None,
                "progress": g.progress,
                "related_entity_ids": g.related_entity_ids,
            }
            for g in result.scalars().all()
        ]

    async def update_progress(self, goal_id: str, progress: float) -> Goal | None:
        """Update goal progress (0.0 to 1.0)."""
        result = await self._db.execute(select(Goal).where(Goal.goal_id == goal_id))
        goal = result.scalar_one_or_none()
        if not goal:
            return None

        goal.progress = max(0.0, min(1.0, progress))
        if goal.progress >= 1.0:
            goal.status = "completed"

        await self._db.commit()
        return goal

    async def complete_goal(self, goal_id: str) -> Goal | None:
        """Mark a goal as completed."""
        return await self.update_progress(goal_id, 1.0)

    async def abandon_goal(self, goal_id: str) -> Goal | None:
        """Mark a goal as abandoned."""
        result = await self._db.execute(select(Goal).where(Goal.goal_id == goal_id))
        goal = result.scalar_one_or_none()
        if not goal:
            return None
        goal.status = "abandoned"
        await self._db.commit()
        return goal

    async def get_goal(self, goal_id: str) -> dict | None:
        """Get a single goal."""
        result = await self._db.execute(select(Goal).where(Goal.goal_id == goal_id))
        g = result.scalar_one_or_none()
        if not g:
            return None
        return {
            "goal_id": g.goal_id,
            "title": g.title,
            "description": g.description,
            "target_date": g.target_date.isoformat() if g.target_date else None,
            "progress": g.progress,
            "status": g.status,
            "related_entity_ids": g.related_entity_ids,
        }
