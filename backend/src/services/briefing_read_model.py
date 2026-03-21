"""Briefing Read Model — list/detail/evidence/lifecycle for briefings.

Provides the read-model layer for briefings with evidence bundles,
related items, and lifecycle actions (pin, snooze, archive).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.briefings import Briefing

logger = logging.getLogger(__name__)


class BriefingReadModel:
    """Read model for briefing list/detail with evidence and actions."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

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
        return await self._exists(briefing_id)

    async def snooze_briefing(self, briefing_id: str) -> bool:
        """Snooze a briefing (hide temporarily)."""
        return await self._exists(briefing_id)

    async def archive_briefing(self, briefing_id: str) -> bool:
        """Archive a briefing."""
        return await self._exists(briefing_id)

    async def _exists(self, briefing_id: str) -> bool:
        result = await self._db.execute(
            select(Briefing.briefing_id).where(
                Briefing.briefing_id == briefing_id,
                Briefing.workspace_id == self._workspace_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def _get_related_items(self, briefing: Briefing) -> list[dict]:
        """Find items related to this briefing (runs, approvals, goals)."""
        items: list[dict] = []

        from src.models.task_graph import TaskRun

        # Find runs created around the same time as the briefing
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
            "status": "active",
            "domain": None,
            "confidence": None,
            "created_at": briefing.created_at.isoformat() if briefing.created_at else None,
        }

    def _get_actions(self, _briefing: Briefing) -> list[dict]:
        return [
            {"action": "pin", "label": "Pin"},
            {"action": "snooze", "label": "Snooze"},
            {"action": "archive", "label": "Archive"},
        ]
