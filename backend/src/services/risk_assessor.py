"""Risk assessment + trust graduation — LLM risk assessor and pure graduation rules.

Components:
- graduate_trust(): Pure function — computes trust level from approval counters
- apply_rejection(): Mutates trust state on rejection with demotion + cooldown
- min_trust_level(): Returns the lower of two trust levels
- get_or_create_trust_state(): DB helper for TrustState upsert
- record_approval_decision(): Feedback loop — updates TrustState on approve/reject
- RiskAssessment, assess_risk(), get_or_assess_risk(): Added in Task 5
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.models import get_haiku_model

logger = logging.getLogger(__name__)


# ── Risk Assessment ──────────────────────────────────────────────

_RISK_SYSTEM_PROMPT = """You assess the contextual risk of actions Jarvis is about to perform
on behalf of the user.

Consider:
- What could go wrong if this action is incorrect or premature?
- Is this reversible? Can it be undone?
- What's the blast radius? Who and how many are affected?
- How sensitive is the content being acted on?
- What's the relationship context? (casual, professional, critical)

You receive:
- The capability being used and its parameters
- The user's goals, relationships, and recent context (from memory)

Output JSON only:
{
  "risk_level": "none | low | medium | high",
  "reasoning": "1-2 sentence human-readable explanation",
  "reversible": true | false,
  "blast_radius": "self | internal | external_single | external_multiple | public"
}"""


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    risk_level: Literal["none", "low", "medium", "high"]
    reasoning: str
    reversible: bool = True
    blast_radius: Literal["self", "internal", "external_single", "external_multiple", "public"] = (
        "self"
    )


def build_risk_cache_key(
    capability: str, step_input: dict, user_context: dict | None = None
) -> str:
    """Build a deterministic cache key from capability + step input + user context."""
    raw = json.dumps(
        {"capability": capability, "input": step_input, "user_context": user_context or {}},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def assess_risk(
    capability: str,
    step_input: dict,
    user_context: dict,
    client: Any,
    model: str | None = None,
) -> RiskAssessment:
    """Call Haiku to assess contextual risk for an action.

    Falls back to medium risk on any failure (API error, invalid JSON, etc.).
    """
    if model is None:
        model = get_haiku_model()
    user_message = json.dumps(
        {
            "capability": capability,
            "parameters": step_input,
            "user_context": user_context,
        },
        default=str,
    )

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=256,
            system=_RISK_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text
        from src.llm_utils import parse_llm_json

        data = parse_llm_json(text)
        return RiskAssessment.model_validate(data)
    except Exception:
        logger.warning(
            "Risk assessment failed for %s, failing closed to high (forces approval)",
            capability,
            exc_info=True,
        )
        # Fail closed: when risk cannot be assessed, treat as high so the trust
        # matrix routes to approval_required at every trust level (incl. autonomous).
        return RiskAssessment(
            risk_level="high",
            reasoning="Fallback — risk assessment failed, failing closed to high",
            reversible=False,
            blast_radius="self",
        )


async def get_or_assess_risk(
    capability: str,
    step_input: dict,
    user_context: dict,
    workspace_id: str,
    client: Any,
    redis: Any,
    model: str | None = None,
) -> RiskAssessment:
    """Redis-cached risk assessment. 24h TTL."""
    if model is None:
        model = get_haiku_model()
    cache_key = build_risk_cache_key(capability, step_input, user_context)
    full_key = f"risk:{workspace_id}:{cache_key}"

    # Try cache
    try:
        cached = await redis.get(full_key)
        if cached:
            return RiskAssessment.model_validate_json(cached)
    except Exception:
        logger.debug("Redis cache read failed for %s", full_key, exc_info=True)

    # Cache miss — call LLM
    assessment = await assess_risk(capability, step_input, user_context, client, model)

    # Store in cache
    try:
        await redis.setex(full_key, 86400, assessment.model_dump_json())
    except Exception:
        logger.debug("Redis cache write failed for %s", full_key, exc_info=True)

    return assessment


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

# Single source of truth for graduation thresholds. To HOLD an earned level a
# capability needs ``>= min_approved`` approvals AND a rejection rate strictly
# below ``max_rejection_rate``. Both graduate_trust() (the decision) and
# _graduation_progress() (the Trust-tab UI) derive from this map, so the two can
# never drift. Keep in sync with the table in CLAUDE.md.
GRADUATION_THRESHOLDS: dict[str, tuple[int, float]] = {
    "trusted": (10, 0.10),  # >= 10 approved and < 10% rejection rate
    "autonomous": (25, 0.05),  # >= 25 approved and < 5% rejection rate
}
# Low-volume entry to "learning": a *clean* record earns it cheaply, before the
# rate-based rules have enough signal to apply.
LEARNING_MIN_APPROVED = 3


def graduate_trust(state) -> str:
    """Compute trust level from approval counters. Pure function — no side effects.

    Thresholds (from ``GRADUATION_THRESHOLDS`` + ``LEARNING_MIN_APPROVED``):
    - 3 approved, 0 rejected → learning
    - 10 approved, <10% rejection rate → trusted
    - 25 approved, <5% rejection rate → autonomous

    A capability that has trusted-tier *volume* but fails its rejection rate
    stays at "learning" (gated, not reset to first_use). Cooldown blocks
    graduation until expiry.
    """
    if state.cooldown_until and datetime.now(timezone.utc) < state.cooldown_until:
        return state.trust_level

    total = state.approved_count + state.rejected_count
    if total == 0:
        return "first_use"

    rejection_rate = state.rejected_count / total
    auto_min, auto_max = GRADUATION_THRESHOLDS["autonomous"]
    trust_min, trust_max = GRADUATION_THRESHOLDS["trusted"]

    if state.approved_count >= auto_min and rejection_rate < auto_max:
        return "autonomous"
    if state.approved_count >= trust_min and rejection_rate < trust_max:
        return "trusted"
    # Trusted-tier volume but failing the rejection rate → hold at learning.
    if state.approved_count >= trust_min:
        return "learning"
    # Low-volume bootstrap: a clean record (no rejections) earns learning.
    if state.approved_count >= LEARNING_MIN_APPROVED and state.rejected_count == 0:
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
    """Get existing TrustState or create a new one.

    Uses SELECT ... FOR UPDATE to serialize concurrent writes to the
    same (workspace_id, capability, risk_level) tuple.
    """
    from src.models.trust_state import TrustState

    result = await db.execute(
        select(TrustState)
        .where(
            TrustState.workspace_id == workspace_id,
            TrustState.capability == capability,
            TrustState.risk_level == risk_level,
        )
        .with_for_update()
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
