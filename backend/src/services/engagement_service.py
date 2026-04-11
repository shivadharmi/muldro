"""Engagement tracking for proactive insight surfaces.

Tracks how users respond to insight surfaces per signal_source × signal_category.
Drives suppression rules:
- 3+ consecutive dismissals → relevance penalty of 0.2
- 5+ consecutive dismissals → auto-suppress (penalty 1.0)
- Any engagement on suppressed type → remove suppression
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.engagement_history import EngagementHistory

logger = logging.getLogger(__name__)

# Suppression thresholds
_PENALTY_THRESHOLD = 3
_SUPPRESS_THRESHOLD = 5
_RELEVANCE_PENALTY = 0.2
_SUPPRESSION_TTL_DAYS = 7


class EngagementService:
    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def _get_or_create(self, signal_source: str, signal_category: str) -> EngagementHistory:
        result = await self._db.execute(
            select(EngagementHistory).where(
                EngagementHistory.workspace_id == self._workspace_id,
                EngagementHistory.signal_source == signal_source,
                EngagementHistory.signal_category == signal_category,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            return row

        row = EngagementHistory(
            workspace_id=self._workspace_id,
            signal_source=signal_source,
            signal_category=signal_category,
            engaged_count=0,
            dismissed_count=0,
            ignored_count=0,
            consecutive_dismissals=0,
            engagement_rate=0.0,
            suppressed=False,
        )
        self._db.add(row)
        try:
            await self._db.flush()
        except IntegrityError:
            await self._db.rollback()
            result = await self._db.execute(
                select(EngagementHistory).where(
                    EngagementHistory.workspace_id == self._workspace_id,
                    EngagementHistory.signal_source == signal_source,
                    EngagementHistory.signal_category == signal_category,
                )
            )
            row = result.scalar_one()
        return row

    async def record_engagement(
        self,
        signal_source: str,
        signal_category: str,
        action: str,
    ) -> None:
        """Record an engagement, dismissal, or ignore event."""
        row = await self._get_or_create(signal_source, signal_category)
        now = datetime.now(timezone.utc)

        if action == "engaged":
            row.engaged_count += 1
            row.consecutive_dismissals = 0
            row.last_engaged_at = now
            if row.suppressed:
                row.suppressed = False
        elif action == "dismissed":
            row.dismissed_count += 1
            row.consecutive_dismissals += 1
            row.last_dismissed_at = now
            if row.consecutive_dismissals >= _SUPPRESS_THRESHOLD:
                row.suppressed = True
        elif action == "ignored":
            row.ignored_count += 1

        total = row.engaged_count + row.dismissed_count + row.ignored_count
        row.engagement_rate = row.engaged_count / max(total, 1)

    async def get_relevance_penalty(self, signal_source: str, signal_category: str) -> float:
        """Return relevance penalty: 0.0, 0.2, or 1.0."""
        result = await self._db.execute(
            select(EngagementHistory).where(
                EngagementHistory.workspace_id == self._workspace_id,
                EngagementHistory.signal_source == signal_source,
                EngagementHistory.signal_category == signal_category,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            return 0.0
        if row.suppressed:
            ttl_cutoff = datetime.now(timezone.utc) - timedelta(days=_SUPPRESSION_TTL_DAYS)
            if row.updated_at and row.updated_at < ttl_cutoff:
                row.suppressed = False
                row.consecutive_dismissals = 0
                return 0.0
            return 1.0
        if row.consecutive_dismissals >= _PENALTY_THRESHOLD:
            return _RELEVANCE_PENALTY
        return 0.0

    async def is_suppressed(self, signal_source: str, signal_category: str) -> bool:
        """Check if a signal source × category is suppressed."""
        result = await self._db.execute(
            select(EngagementHistory).where(
                EngagementHistory.workspace_id == self._workspace_id,
                EngagementHistory.signal_source == signal_source,
                EngagementHistory.signal_category == signal_category,
            )
        )
        row = result.scalar_one_or_none()
        if not row or not row.suppressed:
            return False
        ttl_cutoff = datetime.now(timezone.utc) - timedelta(days=_SUPPRESSION_TTL_DAYS)
        if row.updated_at and row.updated_at < ttl_cutoff:
            row.suppressed = False
            row.consecutive_dismissals = 0
            return False
        return True

    async def get_engagement_context(self) -> str:
        """Build text summary of engagement patterns for relevance assessor."""
        result = await self._db.execute(
            select(EngagementHistory).where(
                EngagementHistory.workspace_id == self._workspace_id,
                EngagementHistory.consecutive_dismissals >= _PENALTY_THRESHOLD,
            )
        )
        rows = result.scalars().all()
        if not rows:
            return ""

        lines = ["User engagement patterns (low engagement signals):"]
        for r in rows:
            status = "SUPPRESSED" if r.suppressed else "low engagement"
            lines.append(
                f"- {r.signal_source}/{r.signal_category}: "
                f"{status}, engagement rate {r.engagement_rate:.0%}, "
                f"{r.consecutive_dismissals} consecutive dismissals"
            )
        return "\n".join(lines)
