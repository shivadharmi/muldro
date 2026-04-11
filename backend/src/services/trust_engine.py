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
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.orchestrator.contracts import PolicyDecision
from src.services.risk_assessor import (
    RiskAssessment,
    get_or_create_trust_state,
    min_trust_level,
)

logger = logging.getLogger(__name__)


def _trust_level_index(level: str) -> int:
    """Index of trust level for ordering comparisons."""
    _levels = ("first_use", "learning", "trusted", "autonomous")
    try:
        return _levels.index(level)
    except ValueError:
        return 0


def _graduation_progress(state) -> dict:
    """Compute graduation progress toward the next trust level.

    Returns dict with: next_level, current, target, percentage.
    """
    level = state.trust_level
    approved = state.approved_count
    rejected = state.rejected_count
    total = approved + rejected

    if level == "first_use":
        return {
            "next_level": "learning",
            "current": approved,
            "target": 3,
            "percentage": min(approved / 3, 1.0) if approved < 3 else 1.0,
            "blocked_by_rejections": rejected > 0,
        }
    elif level == "learning":
        return {
            "next_level": "trusted",
            "current": approved,
            "target": 10,
            "percentage": min(approved / 10, 1.0),
            "blocked_by_rejections": (total > 0 and rejected / total >= 0.10),
        }
    elif level == "trusted":
        return {
            "next_level": "autonomous",
            "current": approved,
            "target": 25,
            "percentage": min(approved / 25, 1.0),
            "blocked_by_rejections": (total > 0 and rejected / total >= 0.05),
        }
    else:
        return {
            "next_level": None,
            "current": approved,
            "target": approved,
            "percentage": 1.0,
            "blocked_by_rejections": False,
        }


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

    # ── Dashboard + Detail Methods ──────────────────────────────

    async def get_trust_dashboard_grouped(self) -> list[dict]:
        """All capabilities with trust levels, progress, and ceilings."""
        from src.integrations.capabilities import CAPABILITY_CATALOG
        from src.models.trust_state import TrustCeiling, TrustState

        result = await self._db.execute(
            select(TrustState).where(TrustState.workspace_id == self._workspace_id)
        )
        states = result.scalars().all()

        ceil_result = await self._db.execute(
            select(TrustCeiling).where(TrustCeiling.workspace_id == self._workspace_id)
        )
        ceilings = {c.capability: c.max_level for c in ceil_result.scalars().all()}

        by_cap: dict[str, list] = {}
        for s in states:
            by_cap.setdefault(s.capability, []).append(s)

        entries = []
        for cap, cap_states in by_cap.items():
            meta = CAPABILITY_CATALOG.get(cap)
            family = meta.family if meta else "unknown"

            best_level = "first_use"
            for s in cap_states:
                if _trust_level_index(s.trust_level) > _trust_level_index(best_level):
                    best_level = s.trust_level

            risk_levels = []
            for s in cap_states:
                risk_levels.append(
                    {
                        "risk_level": s.risk_level,
                        "trust_level": s.trust_level,
                        "approved_count": s.approved_count,
                        "rejected_count": s.rejected_count,
                        "graduation_progress": _graduation_progress(s),
                    }
                )

            entries.append(
                {
                    "capability": cap,
                    "family": str(family),
                    "trust_level": best_level,
                    "ceiling": ceilings.get(cap, "autonomous"),
                    "risk_levels": risk_levels,
                }
            )

        return entries

    async def get_capability_detail(self, capability: str) -> dict:
        """Detailed trust state across all risk levels for one capability."""
        from src.integrations.capabilities import CAPABILITY_CATALOG
        from src.models.trust_state import TrustCeiling, TrustState

        result = await self._db.execute(
            select(TrustState).where(
                TrustState.workspace_id == self._workspace_id,
                TrustState.capability == capability,
            )
        )
        states = result.scalars().all()

        ceil_result = await self._db.execute(
            select(TrustCeiling).where(
                TrustCeiling.workspace_id == self._workspace_id,
                TrustCeiling.capability == capability,
            )
        )
        ceiling = ceil_result.scalar_one_or_none()

        meta = CAPABILITY_CATALOG.get(capability)
        family = str(meta.family) if meta else "unknown"

        risk_levels = []
        for s in states:
            risk_levels.append(
                {
                    "risk_level": s.risk_level,
                    "trust_level": s.trust_level,
                    "approved_count": s.approved_count,
                    "rejected_count": s.rejected_count,
                    "modified_count": s.modified_count,
                    "last_decision_at": (
                        s.last_decision_at.isoformat() if s.last_decision_at else None
                    ),
                    "cooldown_until": (s.cooldown_until.isoformat() if s.cooldown_until else None),
                    "graduation_progress": _graduation_progress(s),
                }
            )

        return {
            "capability": capability,
            "family": family,
            "ceiling": ceiling.max_level if ceiling else "autonomous",
            "risk_levels": risk_levels,
        }

    async def set_ceiling(self, capability: str, max_level: str) -> None:
        """Set or update the trust ceiling for a capability."""
        from src.models.trust_state import TrustCeiling

        result = await self._db.execute(
            select(TrustCeiling).where(
                TrustCeiling.workspace_id == self._workspace_id,
                TrustCeiling.capability == capability,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.max_level = max_level
        else:
            self._db.add(
                TrustCeiling(
                    workspace_id=self._workspace_id,
                    capability=capability,
                    max_level=max_level,
                )
            )
        await self._db.flush()

    async def set_ceilings_batch(self, capabilities: list[str], max_level: str) -> int:
        """Batch-set ceilings for multiple capabilities. Returns count updated."""
        count = 0
        for cap in capabilities:
            await self.set_ceiling(cap, max_level)
            count += 1
        return count

    async def reset_trust_for_capability(self, capability: str) -> None:
        """Reset all trust states for a capability back to first_use."""
        from src.models.trust_state import TrustState

        result = await self._db.execute(
            select(TrustState).where(
                TrustState.workspace_id == self._workspace_id,
                TrustState.capability == capability,
            )
        )
        for state in result.scalars().all():
            state.approved_count = 0
            state.rejected_count = 0
            state.modified_count = 0
            state.trust_level = "first_use"
            state.cooldown_until = None
        await self._db.flush()

    # ── Time-Scoped Ceilings ────────────────────────────────────

    async def get_time_policies(self) -> list[dict]:
        """Get time-scoped ceiling overrides for this workspace."""
        from src.services.settings_service import SettingsService

        svc = SettingsService(self._db)
        policies = await svc.get(self._workspace_id, "trust", "time_policies")
        if not policies or not isinstance(policies, list):
            return []
        return policies

    async def set_time_policies(self, policies: list[dict]) -> None:
        """Set time-scoped ceiling overrides for this workspace."""
        from src.services.settings_service import SettingsService

        svc = SettingsService(self._db)
        await svc.set(self._workspace_id, "trust", "time_policies", policies)
