"""Trust engine — deterministic policy decisions from trust state + risk level.

Implements a 4×4 matrix of (trust_level × risk_level) → PolicyDecision:

                 | none          | low           | medium           | high
    first_use    | approval_req  | approval_req  | approval_req     | approval_req
    learning     | approval_req  | approval_req  | approval_req     | approval_req
    trusted      | auto_notify   | auto_notify   | approval_req     | approval_req
    autonomous   | auto_silent   | auto_silent   | auto_notify      | approval_req

Ceiling: user-set max autonomy per capability caps the effective trust level.
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.orchestrator.contracts import PolicyDecision
from src.services.risk_assessor import (
    RiskAssessment,
    apply_rejection,
    get_or_create_trust_state,
    graduate_trust,
    min_trust_level,
)

logger = logging.getLogger(__name__)


class TrustEngine:
    """Deterministic trust evaluation from TrustState + RiskAssessment."""

    def __init__(self, db: AsyncSession, workspace_id: str = ""):
        self._db = db
        self._workspace_id = workspace_id

    async def evaluate(self, capability: str, risk_assessment: RiskAssessment) -> PolicyDecision:
        """Evaluate trust for a capability + risk assessment → PolicyDecision."""
        risk = risk_assessment.risk_level
        state = await self._get_trust_state(capability, risk)
        ceiling = await self._get_ceiling(capability)
        effective_level = min_trust_level(state.trust_level, ceiling.max_level)

        decision = self._matrix_lookup(effective_level, risk)

        return PolicyDecision(
            decision=decision,
            justification=risk_assessment.reasoning,
            risk_level=risk,
        )

    def _matrix_lookup(self, trust_level: str, risk_level: str) -> str:
        """4×4 matrix: trust_level × risk_level → decision string."""
        if trust_level in ("first_use", "learning"):
            return "approval_required"

        if trust_level == "trusted":
            if risk_level in ("none", "low"):
                return "auto_execute_notify"
            return "approval_required"

        if trust_level == "autonomous":
            if risk_level == "high":
                return "approval_required"
            if risk_level == "medium":
                return "auto_execute_notify"
            return "auto_execute_silent"

        return "approval_required"

    async def _get_trust_state(self, capability: str, risk_level: str):
        """Fetch or create TrustState for this workspace + capability + risk."""
        return await get_or_create_trust_state(self._db, self._workspace_id, capability, risk_level)

    async def _get_ceiling(self, capability: str):
        """Fetch TrustCeiling or return default (autonomous)."""
        from src.models.trust_state import TrustCeiling

        result = await self._db.execute(
            select(TrustCeiling).where(
                TrustCeiling.workspace_id == self._workspace_id,
                TrustCeiling.capability == capability,
            )
        )
        ceiling = result.scalar_one_or_none()
        if ceiling:
            return ceiling

        return SimpleNamespace(max_level="autonomous")

    # ── Compatibility shim for Governor (removed in Spec 2B) ─────

    async def record_decision(
        self, user_id: str, action_type: str, approved: bool, workspace_id: str = ""
    ) -> float:
        """Compatibility shim — Governor still calls this."""
        ws = workspace_id or self._workspace_id
        state = await get_or_create_trust_state(self._db, ws, action_type, "low")
        if approved:
            state.approved_count += 1
        else:
            apply_rejection(state)

        state.last_decision_at = datetime.now(timezone.utc)
        state.trust_level = graduate_trust(state)
        await self._db.flush()

        total = state.approved_count + state.rejected_count
        return state.approved_count / total if total > 0 else 0.0

    async def should_auto_approve(
        self, user_id: str, action_type: str, risk_level: str = "low", workspace_id: str = ""
    ) -> bool:
        """Compatibility shim."""
        ws = workspace_id or self._workspace_id
        state = await get_or_create_trust_state(self._db, ws, action_type, risk_level)

        if risk_level == "high":
            return False

        total = state.approved_count + state.rejected_count
        if total < 5:
            return False

        return state.trust_level in ("trusted", "autonomous")

    async def get_trust_score(
        self, user_id: str, action_type: str, workspace_id: str = ""
    ) -> float:
        """Compatibility shim."""
        ws = workspace_id or self._workspace_id
        state = await get_or_create_trust_state(self._db, ws, action_type, "low")
        total = state.approved_count + state.rejected_count
        return state.approved_count / total if total > 0 else 0.0

    async def get_trust_dashboard(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """Compatibility shim."""
        from src.models.trust_state import TrustState

        ws = workspace_id or self._workspace_id
        result = await self._db.execute(select(TrustState).where(TrustState.workspace_id == ws))
        states = result.scalars().all()
        return [
            {
                "action_type": s.capability,
                "trust_level": s.trust_level,
                "approved_count": s.approved_count,
                "rejected_count": s.rejected_count,
                "risk_level": s.risk_level,
                "last_decision_at": (
                    s.last_decision_at.isoformat() if s.last_decision_at else None
                ),
            }
            for s in states
        ]

    async def reset_trust(
        self, user_id: str, action_type: str | None = None, workspace_id: str = ""
    ) -> None:
        """Compatibility shim."""
        from src.models.trust_state import TrustState

        ws = workspace_id or self._workspace_id
        conditions = [TrustState.workspace_id == ws]
        if action_type:
            conditions.append(TrustState.capability == action_type)
        result = await self._db.execute(select(TrustState).where(*conditions))
        for state in result.scalars().all():
            state.approved_count = 0
            state.rejected_count = 0
            state.modified_count = 0
            state.trust_level = "first_use"
            state.cooldown_until = None
        await self._db.flush()
