"""Risk assessment + trust graduation — LLM risk assessor and pure graduation rules.

Components:
- graduate_trust(): Pure function — computes trust level from approval counters
- apply_rejection(): Mutates trust state on rejection with demotion + cooldown
- min_trust_level(): Returns the lower of two trust levels
- get_or_create_trust_state(): DB helper for TrustState upsert
- record_approval_decision(): Feedback loop — updates TrustState on approve/reject
- RiskAssessment, assess_risk(), get_or_assess_risk(): Added in Task 5
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Trust Level Ordering ─────────────────────────────────────────
TRUST_LEVELS = ("first_use", "learning", "trusted", "autonomous")


def _trust_level_index(level: str) -> int:
    try:
        return TRUST_LEVELS.index(level)
    except ValueError:
        return 0


def min_trust_level(a: str, b: str) -> str:
    """Return the lower of two trust levels."""
    return TRUST_LEVELS[min(_trust_level_index(a), _trust_level_index(b))]


# ── Graduation Rules (pure functions) ────────────────────────────


def graduate_trust(state) -> str:
    """Compute trust level from approval counters. Pure function — no side effects.

    Thresholds:
    - 3 approved, 0 rejected → learning
    - 10 approved, <10% rejection rate → trusted
    - 25 approved, <5% rejection rate → autonomous

    Cooldown blocks graduation until expiry.
    """
    if state.cooldown_until and datetime.now(timezone.utc) < state.cooldown_until:
        return state.trust_level

    total = state.approved_count + state.rejected_count
    if total == 0:
        return "first_use"

    rejection_rate = state.rejected_count / total

    if state.approved_count >= 25 and rejection_rate < 0.05:
        return "autonomous"
    elif state.approved_count >= 10 and rejection_rate < 0.10:
        return "trusted"
    elif state.approved_count >= 10 and rejection_rate >= 0.10:
        return "learning"
    elif state.approved_count >= 3 and state.rejected_count == 0:
        return "learning"

    return "first_use"


def apply_rejection(state) -> None:
    """Apply a rejection to trust state — demotes level + sets cooldown.

    Demotion ladder:
    - autonomous → trusted (72h cooldown)
    - trusted → learning (48h cooldown)
    - learning → first_use (24h cooldown)
    - first_use → first_use (no cooldown)
    """
    state.rejected_count += 1
    now = datetime.now(timezone.utc)

    if state.trust_level == "autonomous":
        state.trust_level = "trusted"
        state.cooldown_until = now + timedelta(hours=72)
    elif state.trust_level == "trusted":
        state.trust_level = "learning"
        state.cooldown_until = now + timedelta(hours=48)
    elif state.trust_level == "learning":
        state.trust_level = "first_use"
        state.cooldown_until = now + timedelta(hours=24)
    # first_use stays first_use, no cooldown


# ── Trust Feedback Loop ──────────────────────────────────────────


async def get_or_create_trust_state(
    db: AsyncSession, workspace_id: str, capability: str, risk_level: str
):
    """Get existing TrustState or create a new one."""
    from src.models.trust_state import TrustState

    result = await db.execute(
        select(TrustState).where(
            TrustState.workspace_id == workspace_id,
            TrustState.capability == capability,
            TrustState.risk_level == risk_level,
        )
    )
    state = result.scalar_one_or_none()
    if state:
        return state

    state = TrustState(
        workspace_id=workspace_id,
        capability=capability,
        risk_level=risk_level,
        approved_count=0,
        rejected_count=0,
        modified_count=0,
        trust_level="first_use",
    )
    db.add(state)
    await db.flush()
    return state


async def record_approval_decision(
    db: AsyncSession,
    workspace_id: str,
    capability: str,
    risk_level: str,
    decision: str,
) -> None:
    """Record an approval/rejection/modification and update trust state.

    Args:
        decision: One of "approved", "rejected", "modified".
    """
    state = await get_or_create_trust_state(db, workspace_id, capability, risk_level)

    if decision == "approved":
        state.approved_count += 1
    elif decision == "rejected":
        apply_rejection(state)
    elif decision == "modified":
        state.modified_count += 1
        state.approved_count += 1  # modified counts as approved with reservation

    state.last_decision_at = datetime.now(timezone.utc)
    state.trust_level = graduate_trust(state)
    await db.flush()

    logger.info(
        "Trust updated: ws=%s cap=%s risk=%s → level=%s (a=%d r=%d m=%d)",
        workspace_id,
        capability,
        risk_level,
        state.trust_level,
        state.approved_count,
        state.rejected_count,
        state.modified_count,
    )
