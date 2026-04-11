"""Briefing Read Model — list/detail/evidence/lifecycle for briefings.

Provides the read-model layer for briefings with evidence bundles,
related items, and lifecycle actions (pin, snooze, archive).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.briefings import Briefing

if TYPE_CHECKING:
    from src.services.tri_search import TriSearchService

logger = logging.getLogger(__name__)


class BriefingReadModel:
    """Read model for briefing list/detail with evidence and actions."""

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: str,
        tri_search: TriSearchService | None = None,
        user_id: str = "",
    ):
        self._db = db
        self._workspace_id = workspace_id
        self._tri_search = tri_search
        self._user_id = user_id

    async def list_briefings(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict]:
        """List briefings with pagination."""
        stmt = (
            select(Briefing)
            .where(Briefing.workspace_id == self._workspace_id)
            .order_by(Briefing.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        result = await self._db.execute(stmt)
        briefings = result.scalars().all()
        return [self._to_list_item(b) for b in briefings]

    async def get_detail(self, briefing_id: str) -> dict | None:
        """Get full briefing detail with evidence and related items."""
        result = await self._db.execute(
            select(Briefing).where(
                Briefing.briefing_id == briefing_id,
                Briefing.workspace_id == self._workspace_id,
            )
        )
        briefing = result.scalar_one_or_none()
        if not briefing:
            return None

        # Build evidence bundle
        from src.services.evidence_bundle import EvidenceBundleService

        evidence_svc = EvidenceBundleService(self._db, self._workspace_id)
        evidence = await evidence_svc.build_for_briefing(briefing_id)

        # Get related items
        related = await self._get_related_items(briefing)

        return {
            **self._to_list_item(briefing),
            "full_text": briefing.full_text,
            "top_priorities": briefing.top_priorities or [],
            "recommended_actions": briefing.recommended_actions or [],
            "evidence": evidence.model_dump(),
            "related_items": related,
            "actions": self._get_actions(briefing),
        }

    async def pin_briefing(self, briefing_id: str) -> bool:
        """Pin a briefing for easy access."""
        briefing = await self._get_briefing(briefing_id)
        if not briefing:
            return False
        briefing.pinned = True
        briefing.status = "pinned"
        await self._db.flush()
        return True

    async def snooze_briefing(self, briefing_id: str, hours: int = 4) -> bool:
        """Snooze a briefing (hide temporarily)."""
        briefing = await self._get_briefing(briefing_id)
        if not briefing:
            return False
        briefing.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=hours)
        briefing.status = "snoozed"
        await self._db.flush()
        return True

    async def archive_briefing(self, briefing_id: str) -> bool:
        """Archive a briefing."""
        briefing = await self._get_briefing(briefing_id)
        if not briefing:
            return False
        briefing.status = "archived"
        await self._db.flush()
        return True

    async def _get_briefing(self, briefing_id: str) -> Briefing | None:
        """Load a single briefing by ID within workspace scope."""
        result = await self._db.execute(
            select(Briefing).where(
                Briefing.briefing_id == briefing_id,
                Briefing.workspace_id == self._workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def _exists(self, briefing_id: str) -> bool:
        result = await self._db.execute(
            select(Briefing.briefing_id).where(
                Briefing.briefing_id == briefing_id,
                Briefing.workspace_id == self._workspace_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def _get_related_items(self, briefing: Briefing) -> list[dict]:
        """Find items related to this briefing via vector similarity.

        Falls back to timestamp proximity if TriSearch is unavailable.
        """
        items: list[dict] = []

        # Prefer semantic search for evidence linking (Issue #26)
        if self._tri_search and briefing.headline:
            try:
                results = await self._tri_search.search(
                    query=briefing.headline,
                    user_id=self._user_id,
                    workspace_id=self._workspace_id,
                    db=self._db,
                    types=["event", "memory", "conversation"],
                    limit=10,
                )
                for r in results:
                    items.append(
                        {
                            "item_type": r.get("result_type", "unknown"),
                            "item_id": r.get("id", ""),
                            "title": r.get("title", ""),
                            "score": r.get("final_score", 0.0),
                        }
                    )
                return items
            except Exception:
                logger.debug(
                    "TriSearch evidence linking failed, falling back",
                    exc_info=True,
                )

        # Fallback: timestamp proximity
        from src.models.task_graph import TaskRun

        if briefing.created_at:
            result = await self._db.execute(
                select(TaskRun)
                .where(
                    TaskRun.workspace_id == self._workspace_id,
                    TaskRun.created_at >= briefing.created_at,
                )
                .order_by(TaskRun.created_at)
                .limit(3)
            )
            for run in result.scalars().all():
                items.append(
                    {
                        "item_type": "run",
                        "item_id": run.run_id,
                        "title": f"Run {run.run_id[:16]}...",
                        "status": run.status,
                    }
                )

        return items

    def _to_list_item(self, briefing: Briefing) -> dict:
        return {
            "briefing_id": briefing.briefing_id,
            "headline": briefing.headline,
            "date": str(briefing.briefing_date) if briefing.briefing_date else None,
            "status": getattr(briefing, "status", "active"),
            "domain": None,
            "confidence": None,
            "created_at": briefing.created_at.isoformat() if briefing.created_at else None,
        }

    def _get_actions(self, briefing: Briefing) -> list[dict]:
        actions = [
            {"action": "snooze", "label": "Snooze"},
            {"action": "archive", "label": "Archive"},
        ]
        if getattr(briefing, "status", "active") != "pinned":
            actions.insert(0, {"action": "pin", "label": "Pin"})
        return actions
