"""Trust engine — builds and tracks trust scores per action type.

Implements graduated autonomy: as the user consistently approves certain
action types, the trust score rises, eventually enabling auto-approval.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.goals import TrustScore

logger = logging.getLogger(__name__)


class TrustEngine:
    """Builds and tracks per-action-type trust scores."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def record_decision(
        self, user_id: str, action_type: str, approved: bool, workspace_id: str = ""
    ) -> float:
        """Record an approval/rejection decision and update trust score."""
        score = await self._get_or_create(user_id, action_type, workspace_id=workspace_id)

        if approved:
            score.approved_count += 1
        else:
            score.rejected_count += 1

        score.last_decision_at = datetime.now(timezone.utc)
        total = score.approved_count + score.rejected_count
        score.trust_score = score.approved_count / total if total > 0 else 0.0

        await self._db.flush()
        logger.info(
            "Trust updated: user=%s action=%s score=%.2f (%d/%d)",
            user_id,
            action_type,
            score.trust_score,
            score.approved_count,
            total,
        )
        return score.trust_score

    async def get_trust_score(
        self, user_id: str, action_type: str, workspace_id: str = ""
    ) -> float:
        """Get the current trust score for an action type."""
        conditions = [
            TrustScore.user_id == user_id,
            TrustScore.action_type == action_type,
        ]
        if workspace_id:
            conditions.append(TrustScore.workspace_id == workspace_id)
        result = await self._db.execute(
            select(TrustScore).where(*conditions)
        )
        score = result.scalar_one_or_none()
        return score.trust_score if score else 0.0

    async def should_auto_approve(
        self, user_id: str, action_type: str, risk_level: str = "low", workspace_id: str = ""
    ) -> bool:
        """Determine if an action should be auto-approved based on trust."""
        conditions = [
            TrustScore.user_id == user_id,
            TrustScore.action_type == action_type,
        ]
        if workspace_id:
            conditions.append(TrustScore.workspace_id == workspace_id)
        result = await self._db.execute(
            select(TrustScore).where(*conditions)
        )
        score = result.scalar_one_or_none()
        if not score:
            return False

        # Never auto-approve high-risk actions
        if risk_level == "high":
            return False

        # Need sufficient history
        total = score.approved_count + score.rejected_count
        if total < 5:
            return False

        return score.trust_score >= score.auto_approve_threshold

    async def get_trust_dashboard(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """Get all trust scores for a user."""
        query = select(TrustScore).where(TrustScore.user_id == user_id)
        if workspace_id:
            query = query.where(TrustScore.workspace_id == workspace_id)
        query = query.order_by(TrustScore.trust_score.desc())
        result = await self._db.execute(query)
        scores = result.scalars().all()
        return [
            {
                "action_type": s.action_type,
                "trust_score": s.trust_score,
                "approved_count": s.approved_count,
                "rejected_count": s.rejected_count,
                "auto_approve_threshold": s.auto_approve_threshold,
                "last_decision_at": s.last_decision_at.isoformat() if s.last_decision_at else None,
            }
            for s in scores
        ]

    async def reset_trust(
        self, user_id: str, action_type: str | None = None, workspace_id: str = ""
    ) -> None:
        """Reset trust scores."""
        if action_type:
            conditions = [
                TrustScore.user_id == user_id,
                TrustScore.action_type == action_type,
            ]
            if workspace_id:
                conditions.append(TrustScore.workspace_id == workspace_id)
            result = await self._db.execute(
                select(TrustScore).where(*conditions)
            )
            score = result.scalar_one_or_none()
            if score:
                score.approved_count = 0
                score.rejected_count = 0
                score.trust_score = 0.0
        else:
            conditions = [TrustScore.user_id == user_id]
            if workspace_id:
                conditions.append(TrustScore.workspace_id == workspace_id)
            result = await self._db.execute(select(TrustScore).where(*conditions))
            for score in result.scalars().all():
                score.approved_count = 0
                score.rejected_count = 0
                score.trust_score = 0.0

        await self._db.flush()

    async def _get_or_create(
        self, user_id: str, action_type: str, workspace_id: str = ""
    ) -> TrustScore:
        conditions = [
            TrustScore.user_id == user_id,
            TrustScore.action_type == action_type,
        ]
        if workspace_id:
            conditions.append(TrustScore.workspace_id == workspace_id)
        result = await self._db.execute(
            select(TrustScore).where(*conditions)
        )
        score = result.scalar_one_or_none()
        if score:
            return score

        score = TrustScore(
            user_id=user_id,
            workspace_id=workspace_id,
            action_type=action_type,
            approved_count=0,
            rejected_count=0,
            trust_score=0.0,
        )
        self._db.add(score)
        await self._db.flush()
        return score
