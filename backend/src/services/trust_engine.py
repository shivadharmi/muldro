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

from src.contracts import PolicyDecision
from src.services.risk_assessor import (
    GRADUATION_THRESHOLDS,
    LEARNING_MIN_APPROVED,
    RiskAssessment,
    _trust_level_index,
    get_or_create_trust_state,
    min_trust_level,
)

logger = logging.getLogger(__name__)


def _graduation_progress(state) -> dict:
    """Compute graduation progress toward the next trust level.

    Returns dict with: next_level, current, target, percentage. Thresholds come
    from ``GRADUATION_THRESHOLDS`` / ``LEARNING_MIN_APPROVED`` (same source of
    truth as ``graduate_trust``) so the UI can never disagree with the gate.
    """
    level = state.trust_level
    approved = state.approved_count
    rejected = state.rejected_count
    total = approved + rejected

    trusted_target, trusted_max_reject = GRADUATION_THRESHOLDS["trusted"]
    autonomous_target, autonomous_max_reject = GRADUATION_THRESHOLDS["autonomous"]

    if level == "first_use":
        result = {
            "next_level": "learning",
            "current": approved,
            "target": LEARNING_MIN_APPROVED,
            "percentage": min(approved / LEARNING_MIN_APPROVED, 1.0),
            "blocked_by_rejections": rejected > 0,
        }
    elif level == "learning":
        result = {
            "next_level": "trusted",
            "current": approved,
            "target": trusted_target,
            "percentage": min(approved / trusted_target, 1.0),
            "blocked_by_rejections": (total > 0 and rejected / total >= trusted_max_reject),
        }
    elif level == "trusted":
        result = {
            "next_level": "autonomous",
            "current": approved,
            "target": autonomous_target,
            "percentage": min(approved / autonomous_target, 1.0),
            "blocked_by_rejections": (total > 0 and rejected / total >= autonomous_max_reject),
        }
    else:
        result = {
            "next_level": None,
            "current": approved,
            "target": approved,
            "percentage": 1.0,
            "blocked_by_rejections": False,
        }

    # Cap percentage when blocked so UI never shows 100% + blocked simultaneously
    if result["blocked_by_rejections"]:
        result["percentage"] = min(result["percentage"], 0.95)
        result["status"] = "blocked_by_rejections"

    return result


class TrustEngine:
    """Deterministic trust evaluation from TrustState + RiskAssessment."""

    def __init__(self, db: AsyncSession, workspace_id: str = ""):
        self._db = db
        self._workspace_id = workspace_id

    async def evaluate(
        self,
        capability: str,
        risk_assessment: RiskAssessment,
        workspace_id: str | None = None,
    ) -> PolicyDecision:
        """Evaluate trust for a capability + risk assessment → PolicyDecision."""
        ws = workspace_id if workspace_id is not None else self._workspace_id
        risk = risk_assessment.risk_level
        state = await self._get_trust_state(capability, risk, workspace_id=ws)
        ceiling = await self._get_ceiling(capability, workspace_id=ws)
        effective_level = min_trust_level(state.trust_level, ceiling.max_level)

        decision = self._matrix_lookup(effective_level, risk)

        return PolicyDecision(
            decision=decision,
            justification=risk_assessment.reasoning,
            risk_level=risk,
            trust_level=state.trust_level,
            effective_trust_level=effective_level,
            approved_count=state.approved_count,
            rejected_count=state.rejected_count,
        )

    async def evaluate_plan_risk(
        self, capability: str, risk_level: str, workspace_id: str | None = None
    ) -> PolicyDecision:
        """Convenience: evaluate trust using a static risk level (no LLM call)."""
        assessment = RiskAssessment(
            risk_level=risk_level,
            reasoning=f"Plan-level risk: {risk_level}",
        )
        return await self.evaluate(capability, assessment, workspace_id=workspace_id)

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

    async def _get_trust_state(self, capability: str, risk_level: str, workspace_id: str = ""):
        """Fetch or create TrustState for this workspace + capability + risk."""
        ws = workspace_id or self._workspace_id
        return await get_or_create_trust_state(self._db, ws, capability, risk_level)

    async def _get_ceiling(self, capability: str, workspace_id: str = ""):
        """Fetch TrustCeiling or return default (autonomous)."""
        from src.models.trust_state import TrustCeiling

        ws = workspace_id or self._workspace_id
        result = await self._db.execute(
            select(TrustCeiling).where(
                TrustCeiling.workspace_id == ws,
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
        """Set or update the trust ceiling for a capability.

        Uses an atomic ``INSERT ... ON CONFLICT DO UPDATE`` so concurrent
        callers (e.g. two browser tabs saving simultaneously) cannot race on
        the ``uq_trust_ceiling`` unique constraint.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from src.models.trust_state import TrustCeiling

        stmt = (
            pg_insert(TrustCeiling)
            .values(
                workspace_id=self._workspace_id,
                capability=capability,
                max_level=max_level,
            )
            .on_conflict_do_update(
                constraint="uq_trust_ceiling",
                set_={"max_level": max_level},
            )
        )
        await self._db.execute(stmt)
        await self._db.flush()

    async def set_ceilings_batch(self, capabilities: list[str], max_level: str) -> int:
        """Batch-set ceilings for multiple capabilities. Returns count updated."""
        from src.models.trust_state import TrustCeiling

        # Load all existing ceilings in one query
        result = await self._db.execute(
            select(TrustCeiling).where(
                TrustCeiling.workspace_id == self._workspace_id,
                TrustCeiling.capability.in_(capabilities),
            )
        )
        existing = {c.capability: c for c in result.scalars().all()}

        for cap in capabilities:
            if cap in existing:
                existing[cap].max_level = max_level
            else:
                self._db.add(
                    TrustCeiling(
                        workspace_id=self._workspace_id,
                        capability=cap,
                        max_level=max_level,
                    )
                )

        await self._db.flush()
        return len(capabilities)

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
