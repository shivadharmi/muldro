"""InitiativeScorer — decides when Jarvis should proactively act.

Computes a composite "initiative score" from event importance, urgency,
goal relevance, and entity context. When the score exceeds the auto-plan
threshold, Jarvis creates a plan without being asked.

This is the core of Phase 7: Proactive Autonomy.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from src.models.events import NormalizedEvent
    from src.services.goal_tracker import GoalTracker
    from src.services.memory_service import MemoryService
    from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)

# Weights for the composite initiative score
INITIATIVE_WEIGHTS = {
    "importance": 0.30,
    "urgency": 0.25,
    "goal_relevance": 0.20,
    "entity_significance": 0.15,
    "novelty": 0.10,
}

# Default threshold — events scoring above this auto-create plans
DEFAULT_AUTO_PLAN_THRESHOLD = 0.70


@dataclass(frozen=True)
class InitiativeResult:
    """Result of initiative scoring."""

    score: float
    should_plan: bool
    should_notify: bool
    signals: dict


class InitiativeScorer:
    """Score events for proactive action."""

    def __init__(
        self,
        db: AsyncSession,
        world_model: "WorldModel | None" = None,
        memory_service: "MemoryService | None" = None,
        goal_tracker: "GoalTracker | None" = None,
        auto_plan_threshold: float = DEFAULT_AUTO_PLAN_THRESHOLD,
        notify_threshold: float = 0.50,
    ):
        self._db = db
        self._world_model = world_model
        self._memory_service = memory_service
        self._goal_tracker = goal_tracker
        self._auto_plan_threshold = auto_plan_threshold
        self._notify_threshold = notify_threshold

    async def score(self, event: "NormalizedEvent", user_id: str) -> InitiativeResult:
        """Compute initiative score for a processed event."""
        importance = event.importance_score or 0.0
        urgency = event.urgency_score or 0.0

        goal_relevance = await self._compute_goal_relevance(event, user_id)
        entity_significance = await self._compute_entity_significance(event, user_id)
        novelty = await self._compute_novelty(event, user_id)

        score = (
            INITIATIVE_WEIGHTS["importance"] * importance
            + INITIATIVE_WEIGHTS["urgency"] * urgency
            + INITIATIVE_WEIGHTS["goal_relevance"] * goal_relevance
            + INITIATIVE_WEIGHTS["entity_significance"] * entity_significance
            + INITIATIVE_WEIGHTS["novelty"] * novelty
        )

        signals = {
            "importance": importance,
            "urgency": urgency,
            "goal_relevance": goal_relevance,
            "entity_significance": entity_significance,
            "novelty": novelty,
        }

        # Boost score for priority signals
        importance_signals = event.importance_signals or {}
        if importance_signals.get("from_priority_person"):
            score = min(1.0, score + 0.15)
            signals["boosted_by"] = "priority_person"
        if importance_signals.get("contains_deadline"):
            score = min(1.0, score + 0.10)
            signals["boosted_by"] = signals.get("boosted_by", "") + ",deadline"

        should_plan = score >= self._auto_plan_threshold
        should_notify = score >= self._notify_threshold

        logger.info(
            "Initiative score for %s: %.3f (plan=%s, notify=%s)",
            event.event_id,
            score,
            should_plan,
            should_notify,
        )

        return InitiativeResult(
            score=score,
            should_plan=should_plan,
            should_notify=should_notify,
            signals=signals,
        )

    async def _compute_goal_relevance(self, event: "NormalizedEvent", user_id: str) -> float:
        """Check if the event relates to any active goals."""
        if not self._goal_tracker:
            return 0.0

        try:
            goals = await self._goal_tracker.get_active_goals(user_id)
            if not goals:
                return 0.0

            event_text = f"{event.title or ''} {event.summary or ''}".lower()
            max_relevance = 0.0
            for goal in goals:
                title = (goal.get("title") or "").lower()
                if not title:
                    continue
                # Simple keyword overlap check
                words = title.split()
                matches = sum(1 for w in words if w in event_text)
                if words:
                    relevance = min(1.0, matches / max(len(words), 1))
                    max_relevance = max(max_relevance, relevance)

            return max_relevance
        except Exception:
            logger.debug("Goal relevance check failed", exc_info=True)
            return 0.0

    async def _compute_entity_significance(self, event: "NormalizedEvent", user_id: str) -> float:
        """Check if actors in the event are high-importance entities."""
        if not self._world_model or not event.actor_entities:
            return 0.0

        try:
            max_significance = 0.0
            for actor in event.actor_entities:
                query = actor.get("email") or actor.get("name", "")
                if not query:
                    continue
                entities = await self._world_model.find_entity(user_id, query)
                if entities:
                    ent = entities[0]
                    importance = ent.get("importance_score", 0.5)
                    max_significance = max(max_significance, importance)

            return max_significance
        except Exception:
            logger.debug("Entity significance check failed", exc_info=True)
            return 0.0

    async def _compute_novelty(self, event: "NormalizedEvent", user_id: str) -> float:
        """Estimate how novel/new this information is."""
        if not self._memory_service:
            return 0.5  # Unknown novelty = medium

        try:
            query = event.title or event.summary or ""
            if not query:
                return 0.5

            memories = await self._memory_service.retrieve(user_id, query, max_results=3)
            if not memories:
                return 0.9  # No related memories = highly novel

            # If we have very similar memories, it's not novel
            top_score = memories[0].get("relevance_score", 0.5)
            return max(0.0, 1.0 - top_score)
        except Exception:
            logger.debug("Novelty check failed", exc_info=True)
            return 0.5
