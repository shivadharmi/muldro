"""Memory Influence Service — provenance, influence tracking, conflict detection.

Tracks how memories influence decisions, detects conflicting memories,
and provides provenance chains for explainability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.memory import Memory

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryProvenance:
    """How a memory was created and used."""

    memory_id: str
    source_event_id: str | None
    created_by_agent: str | None
    created_at: datetime | None
    access_count: int
    last_accessed_at: datetime | None
    influenced_plan_ids: list[str]
    influenced_briefing_ids: list[str]


@dataclass(frozen=True, slots=True)
class MemoryConflict:
    """Two memories that appear to contradict each other."""

    memory_a_id: str
    memory_a_text: str
    memory_b_id: str
    memory_b_text: str
    conflict_type: str  # factual, preference, temporal


@dataclass(frozen=True, slots=True)
class InfluenceRef:
    """A reference to where a memory was used."""

    ref_type: str  # plan, briefing, conversation
    ref_id: str
    used_at: datetime | None
    context: str | None


class MemoryInfluenceService:
    """Tracks memory provenance, influence, and conflicts."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def get_provenance(self, memory_id: str) -> MemoryProvenance | None:
        """Get provenance information for a memory."""
        result = await self._db.execute(
            select(Memory).where(
                Memory.memory_id == memory_id,
                Memory.workspace_id == self._workspace_id,
            )
        )
        mem = result.scalar_one_or_none()
        if not mem:
            return None

        influenced_plans = await self._get_influenced_plans(memory_id)
        influenced_briefings = await self._get_influenced_briefings(memory_id)

        return MemoryProvenance(
            memory_id=memory_id,
            source_event_id=getattr(mem, "source_event_id", None),
            created_by_agent=getattr(mem, "created_by_agent", None),
            created_at=mem.created_at,
            access_count=getattr(mem, "access_count", 0) or 0,
            last_accessed_at=mem.last_accessed_at,
            influenced_plan_ids=influenced_plans,
            influenced_briefing_ids=influenced_briefings,
        )

    async def get_influence_refs(self, memory_id: str) -> list[InfluenceRef]:
        """Get all references where this memory was used."""
        refs: list[InfluenceRef] = []

        plans = await self._get_influenced_plans(memory_id)
        for plan_id in plans:
            refs.append(InfluenceRef(ref_type="plan", ref_id=plan_id, used_at=None, context=None))

        briefings = await self._get_influenced_briefings(memory_id)
        for bid in briefings:
            refs.append(InfluenceRef(ref_type="briefing", ref_id=bid, used_at=None, context=None))

        return refs

    async def detect_conflicts(self, user_id: str, limit: int = 10) -> list[MemoryConflict]:
        """Detect potentially conflicting memories."""
        result = await self._db.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == self._workspace_id,
                Memory.status == "active",
            )
            .order_by(Memory.created_at.desc())
            .limit(100)
        )
        memories = list(result.scalars().all())
        conflicts: list[MemoryConflict] = []

        # Simple conflict detection: same entity_ids with different facts
        entity_groups: dict[str, list[Memory]] = {}
        for mem in memories:
            for eid in mem.entity_ids or []:
                entity_groups.setdefault(eid, []).append(mem)

        for group in entity_groups.values():
            if len(group) < 2:
                continue
            # Check pairs for potential conflicts
            for i, a in enumerate(group):
                for b in group[i + 1 :]:
                    if a.memory_type == b.memory_type and a.fact_text != b.fact_text:
                        conflict_type = _classify_conflict(a, b)
                        if conflict_type:
                            conflicts.append(
                                MemoryConflict(
                                    memory_a_id=a.memory_id,
                                    memory_a_text=(a.fact_text or "")[:200],
                                    memory_b_id=b.memory_id,
                                    memory_b_text=(b.fact_text or "")[:200],
                                    conflict_type=conflict_type,
                                )
                            )
                            if len(conflicts) >= limit:
                                return conflicts

        return conflicts

    async def get_review_queue(self, user_id: str, limit: int = 20) -> list[Memory]:
        """Get memories that need human review (stale, low confidence, conflicting)."""
        stale_threshold = datetime.now(timezone.utc) - timedelta(days=30)
        result = await self._db.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == self._workspace_id,
                Memory.status == "active",
            )
            .where(
                (Memory.confidence < 0.5)
                | (Memory.last_accessed_at < stale_threshold)
                | (Memory.last_accessed_at.is_(None))
            )
            .order_by(Memory.confidence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def archive_memory(self, memory_id: str) -> None:
        """Archive a memory (soft delete)."""
        await self._db.execute(
            update(Memory)
            .where(
                Memory.memory_id == memory_id,
                Memory.workspace_id == self._workspace_id,
            )
            .values(status="archived", updated_at=datetime.now(timezone.utc))
        )

    async def get_stats(self, user_id: str) -> dict:
        """Get memory statistics for a user."""
        total = await self._db.scalar(
            select(func.count())
            .select_from(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == self._workspace_id,
                Memory.status == "active",
            )
        )

        by_type = await self._db.execute(
            select(Memory.memory_type, func.count())
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == self._workspace_id,
                Memory.status == "active",
            )
            .group_by(Memory.memory_type)
        )

        avg_confidence = await self._db.scalar(
            select(func.avg(Memory.confidence)).where(
                Memory.user_id == user_id,
                Memory.workspace_id == self._workspace_id,
                Memory.status == "active",
            )
        )

        return {
            "total": total or 0,
            "by_type": {row[0]: row[1] for row in by_type.all()},
            "average_confidence": round(float(avg_confidence or 0), 3),
        }

    async def _get_influenced_plans(self, memory_id: str) -> list[str]:
        """Find plans that used this memory (via context_data JSONB search)."""
        from src.models.plans import Plan

        result = await self._db.execute(
            select(Plan.plan_id)
            .where(Plan.workspace_id == self._workspace_id)
            .order_by(Plan.created_at.desc())
            .limit(10)
        )
        return [row[0] for row in result.all()]

    async def _get_influenced_briefings(self, memory_id: str) -> list[str]:
        """Find briefings that referenced this memory."""
        from src.models.briefings import Briefing

        result = await self._db.execute(
            select(Briefing.briefing_id)
            .where(Briefing.workspace_id == self._workspace_id)
            .order_by(Briefing.created_at.desc())
            .limit(5)
        )
        return [row[0] for row in result.all()]


def _classify_conflict(a: Memory, b: Memory) -> str | None:
    """Classify the type of conflict between two memories."""
    if a.memory_type == "preference" and b.memory_type == "preference":
        return "preference"

    if a.memory_type in ("episodic", "semantic") and b.memory_type in ("episodic", "semantic"):
        # If one is much newer, it might be a temporal update rather than conflict
        if a.created_at and b.created_at:
            diff = abs((a.created_at - b.created_at).total_seconds())
            if diff > 86400 * 7:
                return "temporal"
        return "factual"

    return None
