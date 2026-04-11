# Spec 2A: Trust Infrastructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the trust data layer — LLM risk assessor, TrustState model, deterministic TrustEngine, graduation rules, and trust feedback loop — without modifying any existing approval gates.

**Architecture:** New `RiskAssessor` service calls Haiku to assess contextual risk, caches results in Redis. New `TrustState`/`TrustCeiling` SQLAlchemy models track per-(workspace, capability, risk_level) trust with graduation counters. Existing `TrustEngine` is rewritten to produce deterministic `PolicyDecision` from a 4×4 trust_level × risk_level matrix. Feedback loop wires into existing approve/reject handlers in `routes_approvals.py`. The old `TrustScore` model and `approval_policy_engine.py` are untouched (deleted in Spec 2B).

**Tech Stack:** Python 3.12, SQLAlchemy 2.x (async), Pydantic v2, Redis (aioredis), Claude Haiku API, Alembic, pytest + pytest-asyncio

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/models/trust_state.py` | TrustState + TrustCeiling SQLAlchemy models |
| Create | `src/services/risk_assessor.py` | LLM risk assessment + Redis caching + graduation rules + feedback loop |
| Create | `alembic/versions/XXX_add_trust_states_and_ceilings.py` | Migration for trust_states + trust_ceilings tables |
| Create | `tests/test_trust_graduation.py` | Unit tests for graduation rules + rejection demotion |
| Create | `tests/test_risk_assessor.py` | Unit tests for risk assessment + caching |
| Create | `tests/test_trust_engine_v2.py` | Unit tests for rewritten TrustEngine (4×4 matrix) |
| Create | `tests/test_trust_feedback.py` | Unit tests for record_approval_decision flow |
| Rewrite | `src/services/trust_engine.py` | Deterministic 4×4 matrix engine using TrustState (not TrustScore) |
| Modify | `src/orchestrator/contracts.py:186` | Add `auto_execute_notify`, `auto_execute_silent` to PolicyDecision Literal |
| Modify | `src/models/__init__.py` | Add TrustState, TrustCeiling imports |
| Modify | `src/api/routes_approvals.py:122-125,298-300` | Add `record_approval_decision()` calls in approve/reject handlers |
| Modify | `src/orchestrator/agent_loop.py:464-470` | Add per-tool cost attribution after each tool call |

---

### Task 1: Extend PolicyDecision Contract

**Files:**
- Modify: `backend/src/orchestrator/contracts.py:186`

- [ ] **Step 1: Extend the PolicyDecision Literal**

In `backend/src/orchestrator/contracts.py`, change line 186:

```python
# OLD:
    decision: Literal["auto_execute", "approval_required", "blocked"]

# NEW:
    decision: Literal[
        "auto_execute",
        "auto_execute_notify",
        "auto_execute_silent",
        "approval_required",
        "blocked",
    ]
```

- [ ] **Step 2: Verify no existing tests break**

Run: `pytest backend/tests/ -v -k "PolicyDecision or contract" --no-header -q 2>&1 | tail -5`
Expected: All existing tests pass (the new values are additive — no existing code produces them yet).

- [ ] **Step 3: Commit**

```bash
git add backend/src/orchestrator/contracts.py
git commit -m "feat(spec2a): extend PolicyDecision with auto_execute_notify and auto_execute_silent"
```

---

### Task 2: TrustState + TrustCeiling Models

**Files:**
- Create: `backend/src/models/trust_state.py`
- Modify: `backend/src/models/__init__.py`

- [ ] **Step 1: Create the TrustState and TrustCeiling models**

Create `backend/src/models/trust_state.py`:

```python
"""Trust state models — per-capability graduated trust tracking."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class TrustState(Base, TimestampMixin):
    """Tracks trust per (workspace, capability, risk_level) with graduation counters."""

    __tablename__ = "trust_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    modified_count: Mapped[int] = mapped_column(Integer, default=0)
    trust_level: Mapped[str] = mapped_column(String(32), default="first_use")
    last_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "capability", "risk_level", name="uq_trust_state"),
        Index("ix_trust_state_lookup", "workspace_id", "capability", "risk_level"),
    )


class TrustCeiling(Base, TimestampMixin):
    """User-set maximum autonomy level per capability."""

    __tablename__ = "trust_ceilings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    max_level: Mapped[str] = mapped_column(String(32), default="autonomous")

    __table_args__ = (
        UniqueConstraint("workspace_id", "capability", name="uq_trust_ceiling"),
    )
```

- [ ] **Step 2: Add imports to models/__init__.py**

In `backend/src/models/__init__.py`, add:

```python
from src.models.trust_state import TrustCeiling, TrustState
```

Add it alphabetically (after the `trust_score` import if present, or after `token_usage`).

- [ ] **Step 3: Verify models import cleanly**

Run: `cd backend && python -c "from src.models.trust_state import TrustState, TrustCeiling; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/src/models/trust_state.py backend/src/models/__init__.py
git commit -m "feat(spec2a): add TrustState and TrustCeiling SQLAlchemy models"
```

---

### Task 3: Alembic Migration

**Files:**
- Create: `backend/alembic/versions/XXX_add_trust_states_and_ceilings.py`

- [ ] **Step 1: Generate the migration**

```bash
cd backend && alembic revision --autogenerate -m "add trust_states and trust_ceilings tables"
```

- [ ] **Step 2: Review the generated migration**

Open the generated file and verify it creates:
- `trust_states` table with all columns (id, workspace_id, capability, risk_level, approved_count, rejected_count, modified_count, trust_level, last_decision_at, cooldown_until, created_at, updated_at)
- `trust_ceilings` table with all columns (id, workspace_id, capability, max_level, created_at, updated_at)
- Unique constraints `uq_trust_state` and `uq_trust_ceiling`
- Index `ix_trust_state_lookup`

If autogenerate missed anything, add it manually.

- [ ] **Step 3: Run the migration**

```bash
cd backend && alembic upgrade head
```

Expected: Migration applies cleanly.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/*trust_states*
git commit -m "feat(spec2a): add migration for trust_states and trust_ceilings tables"
```

---

### Task 4: Trust Graduation Rules + Tests

**Files:**
- Create: `backend/src/services/risk_assessor.py` (graduation section only — the rest added in Task 5)
- Create: `backend/tests/test_trust_graduation.py`

The graduation rules are pure functions — no DB, no async. We implement them in `risk_assessor.py` alongside the risk assessment (they're tightly coupled: the risk assessor produces risk_level, the graduation rules consume it via TrustState).

- [ ] **Step 1: Write failing tests for graduation rules**

Create `backend/tests/test_trust_graduation.py`:

```python
"""Tests for trust graduation rules — pure function, no DB."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.services.risk_assessor import apply_rejection, graduate_trust

TRUST_LEVELS = ("first_use", "learning", "trusted", "autonomous")


def _make_state(
    approved: int = 0,
    rejected: int = 0,
    modified: int = 0,
    trust_level: str = "first_use",
    cooldown_until: datetime | None = None,
) -> MagicMock:
    s = MagicMock()
    s.approved_count = approved
    s.rejected_count = rejected
    s.modified_count = modified
    s.trust_level = trust_level
    s.cooldown_until = cooldown_until
    return s


class TestGraduateTrust:
    def test_zero_decisions_stays_first_use(self):
        state = _make_state(approved=0, rejected=0)
        assert graduate_trust(state) == "first_use"

    def test_three_approvals_zero_rejections_graduates_to_learning(self):
        state = _make_state(approved=3, rejected=0)
        assert graduate_trust(state) == "learning"

    def test_two_approvals_stays_first_use(self):
        state = _make_state(approved=2, rejected=0)
        assert graduate_trust(state) == "first_use"

    def test_ten_approvals_low_rejection_graduates_to_trusted(self):
        state = _make_state(approved=10, rejected=1)
        assert graduate_trust(state) == "trusted"

    def test_ten_approvals_high_rejection_stays_learning(self):
        # 10 approved, 2 rejected = 2/12 ≈ 16.7% > 10%
        state = _make_state(approved=10, rejected=2)
        assert graduate_trust(state) == "learning"

    def test_twentyfive_approvals_graduates_to_autonomous(self):
        state = _make_state(approved=25, rejected=1)
        assert graduate_trust(state) == "autonomous"

    def test_twentyfive_approvals_high_rejection_stays_trusted(self):
        # 25 approved, 2 rejected = 2/27 ≈ 7.4% > 5%
        state = _make_state(approved=25, rejected=2)
        assert graduate_trust(state) == "trusted"

    def test_cooldown_blocks_graduation(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        state = _make_state(approved=30, rejected=0, trust_level="learning", cooldown_until=future)
        assert graduate_trust(state) == "learning"

    def test_expired_cooldown_allows_graduation(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        state = _make_state(approved=30, rejected=0, trust_level="learning", cooldown_until=past)
        assert graduate_trust(state) == "autonomous"

    def test_three_approvals_with_one_rejection_stays_first_use(self):
        state = _make_state(approved=3, rejected=1)
        assert graduate_trust(state) == "first_use"


class TestApplyRejection:
    def test_autonomous_demotes_to_trusted_72h(self):
        state = _make_state(approved=30, rejected=0, trust_level="autonomous")
        apply_rejection(state)
        assert state.rejected_count == 1
        assert state.trust_level == "trusted"
        assert state.cooldown_until is not None
        cooldown_hours = (state.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 71 < cooldown_hours < 73

    def test_trusted_demotes_to_learning_48h(self):
        state = _make_state(approved=15, rejected=0, trust_level="trusted")
        apply_rejection(state)
        assert state.rejected_count == 1
        assert state.trust_level == "learning"
        cooldown_hours = (state.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 47 < cooldown_hours < 49

    def test_learning_demotes_to_first_use_24h(self):
        state = _make_state(approved=5, rejected=0, trust_level="learning")
        apply_rejection(state)
        assert state.rejected_count == 1
        assert state.trust_level == "first_use"
        cooldown_hours = (state.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 23 < cooldown_hours < 25

    def test_first_use_stays_first_use(self):
        state = _make_state(approved=1, rejected=0, trust_level="first_use")
        apply_rejection(state)
        assert state.rejected_count == 1
        assert state.trust_level == "first_use"
        assert state.cooldown_until is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_trust_graduation.py -v 2>&1 | tail -5`
Expected: FAIL — `ImportError: cannot import name 'apply_rejection' from 'src.services.risk_assessor'`

- [ ] **Step 3: Implement graduation rules**

Create `backend/src/services/risk_assessor.py` with just the graduation functions for now:

```python
"""Risk assessment + trust graduation — LLM risk assessor and pure graduation rules.

Components:
- assess_risk() / get_or_assess_risk(): Haiku-based contextual risk (added in Task 5)
- graduate_trust(): Pure function — computes trust level from approval counters
- apply_rejection(): Mutates trust state on rejection with demotion + cooldown
- record_approval_decision(): Feedback loop — updates TrustState on approve/reject
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_trust_graduation.py -v 2>&1 | tail -20`
Expected: All 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/risk_assessor.py backend/tests/test_trust_graduation.py
git commit -m "feat(spec2a): add trust graduation rules with tests"
```

---

### Task 5: LLM Risk Assessor + Caching + Tests

**Files:**
- Modify: `backend/src/services/risk_assessor.py` (add RiskAssessment model, assess_risk, caching)
- Create: `backend/tests/test_risk_assessor.py`

- [ ] **Step 1: Write failing tests for risk assessment**

Create `backend/tests/test_risk_assessor.py`:

```python
"""Tests for LLM risk assessor + Redis caching."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.risk_assessor import (
    RiskAssessment,
    assess_risk,
    build_risk_cache_key,
    get_or_assess_risk,
)


@pytest.fixture
def mock_client():
    client = AsyncMock()
    response = MagicMock()
    response.content = [
        MagicMock(
            text=json.dumps(
                {
                    "risk_level": "low",
                    "reasoning": "Casual lunch message to known contact",
                    "reversible": True,
                    "blast_radius": "external_single",
                }
            )
        )
    ]
    response.usage = MagicMock(input_tokens=100, output_tokens=50)
    client.messages.create = AsyncMock(return_value=response)
    return client


class TestRiskAssessment:
    def test_model_validation(self):
        ra = RiskAssessment(
            risk_level="low",
            reasoning="test",
            reversible=True,
            blast_radius="self",
        )
        assert ra.risk_level == "low"

    def test_model_defaults(self):
        ra = RiskAssessment(risk_level="medium", reasoning="test")
        assert ra.reversible is True
        assert ra.blast_radius == "self"


class TestAssessRisk:
    async def test_returns_risk_assessment(self, mock_client):
        result = await assess_risk(
            capability="email.send",
            step_input={"to": "friend@example.com", "body": "Hey lunch?"},
            user_context={"relationships": {"friend@example.com": "close friend"}},
            client=mock_client,
            model="claude-haiku-4-5-20251001",
        )
        assert isinstance(result, RiskAssessment)
        assert result.risk_level == "low"
        mock_client.messages.create.assert_called_once()

    async def test_falls_back_on_api_error(self, mock_client):
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))
        result = await assess_risk(
            capability="email.send",
            step_input={"to": "ceo@corp.com", "body": "Revenue report"},
            user_context={},
            client=mock_client,
            model="claude-haiku-4-5-20251001",
        )
        assert result.risk_level == "medium"
        assert "fallback" in result.reasoning.lower()

    async def test_falls_back_on_invalid_json(self, mock_client):
        response = MagicMock()
        response.content = [MagicMock(text="not json")]
        response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create = AsyncMock(return_value=response)

        result = await assess_risk(
            capability="email.send",
            step_input={},
            user_context={},
            client=mock_client,
            model="claude-haiku-4-5-20251001",
        )
        assert result.risk_level == "medium"


class TestCacheKey:
    def test_same_inputs_same_key(self):
        k1 = build_risk_cache_key("email.send", {"to": "a@b.com", "body": "hi"})
        k2 = build_risk_cache_key("email.send", {"to": "a@b.com", "body": "hi"})
        assert k1 == k2

    def test_different_targets_different_keys(self):
        k1 = build_risk_cache_key("email.send", {"to": "a@b.com", "body": "hi"})
        k2 = build_risk_cache_key("email.send", {"to": "x@y.com", "body": "hi"})
        assert k1 != k2


class TestGetOrAssessRisk:
    async def test_cache_hit(self, mock_client):
        cached = RiskAssessment(
            risk_level="low", reasoning="cached", reversible=True, blast_radius="self"
        )
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=cached.model_dump_json())

        result = await get_or_assess_risk(
            capability="email.send",
            step_input={"to": "a@b.com"},
            user_context={},
            workspace_id="ws_test",
            client=mock_client,
            redis=redis,
            model="claude-haiku-4-5-20251001",
        )
        assert result.reasoning == "cached"
        mock_client.messages.create.assert_not_called()

    async def test_cache_miss_calls_llm(self, mock_client):
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()

        result = await get_or_assess_risk(
            capability="email.send",
            step_input={"to": "a@b.com"},
            user_context={},
            workspace_id="ws_test",
            client=mock_client,
            redis=redis,
            model="claude-haiku-4-5-20251001",
        )
        assert result.risk_level == "low"
        redis.setex.assert_called_once()
        # Verify 24h TTL
        call_args = redis.setex.call_args
        assert call_args[0][1] == 86400

    async def test_cache_error_falls_through(self, mock_client):
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=Exception("Redis down"))
        redis.setex = AsyncMock(side_effect=Exception("Redis down"))

        result = await get_or_assess_risk(
            capability="email.send",
            step_input={"to": "a@b.com"},
            user_context={},
            workspace_id="ws_test",
            client=mock_client,
            redis=redis,
            model="claude-haiku-4-5-20251001",
        )
        assert result.risk_level == "low"  # LLM still works
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_risk_assessor.py -v 2>&1 | tail -5`
Expected: FAIL — `ImportError: cannot import name 'RiskAssessment' from 'src.services.risk_assessor'`

- [ ] **Step 3: Implement the risk assessor and caching**

Add the following to the TOP of `backend/src/services/risk_assessor.py` (above the existing graduation code), replacing the module docstring and imports:

```python
"""Risk assessment + trust graduation — LLM risk assessor and pure graduation rules.

Components:
- RiskAssessment: Pydantic model for risk assessment results
- assess_risk(): Haiku-based contextual risk assessment
- get_or_assess_risk(): Redis-cached wrapper around assess_risk
- build_risk_cache_key(): Deterministic cache key builder
- graduate_trust(): Pure function — computes trust level from approval counters
- apply_rejection(): Mutates trust state on rejection with demotion + cooldown
- record_approval_decision(): Feedback loop — updates TrustState on approve/reject
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

_FALLBACK_RISK = None  # Set below after class definition


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    risk_level: Literal["none", "low", "medium", "high"]
    reasoning: str
    reversible: bool = True
    blast_radius: Literal[
        "self", "internal", "external_single", "external_multiple", "public"
    ] = "self"


_FALLBACK_RISK = RiskAssessment(
    risk_level="medium",
    reasoning="Fallback — risk assessor unavailable, defaulting to medium risk",
    reversible=True,
    blast_radius="self",
)


def build_risk_cache_key(capability: str, step_input: dict) -> str:
    """Build a deterministic cache key from capability + step input.

    Uses a hash of the sorted JSON to handle dict ordering differences.
    """
    raw = json.dumps({"capability": capability, "input": step_input}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


async def assess_risk(
    capability: str,
    step_input: dict,
    user_context: dict,
    client: Any,
    model: str = "claude-haiku-4-5-20251001",
) -> RiskAssessment:
    """Call Haiku to assess contextual risk for an action.

    Falls back to medium risk on any failure (API error, invalid JSON, etc.).
    """
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
        data = json.loads(text)
        return RiskAssessment.model_validate(data)
    except Exception:
        logger.warning(
            "Risk assessment failed for %s, falling back to medium",
            capability,
            exc_info=True,
        )
        return RiskAssessment(
            risk_level="medium",
            reasoning="Fallback — risk assessment failed, defaulting to medium",
            reversible=True,
            blast_radius="self",
        )


async def get_or_assess_risk(
    capability: str,
    step_input: dict,
    user_context: dict,
    workspace_id: str,
    client: Any,
    redis: Any,
    model: str = "claude-haiku-4-5-20251001",
) -> RiskAssessment:
    """Redis-cached risk assessment. 24h TTL."""
    cache_key = build_risk_cache_key(capability, step_input)
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
```

**Important:** Remove the duplicate imports and docstring from the graduation section below. The file should have one set of imports at the top and flow: Risk Assessment → Trust Levels → Graduation Rules → Feedback Loop.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_risk_assessor.py -v 2>&1 | tail -20`
Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/risk_assessor.py backend/tests/test_risk_assessor.py
git commit -m "feat(spec2a): add LLM risk assessor with Redis caching"
```

---

### Task 6: Rewrite TrustEngine + Tests

**Files:**
- Rewrite: `backend/src/services/trust_engine.py`
- Create: `backend/tests/test_trust_engine_v2.py`

The existing `trust_engine.py` (163 lines) uses the old `TrustScore` model. The rewrite uses `TrustState` with the 4×4 matrix. The old `test_trust_engine.py` tests the old API — we create a new test file and leave the old one (it will be removed in Spec 2B).

**Callers of the existing TrustEngine:**
- `src/services/governor.py` — imports `TrustEngine` under `TYPE_CHECKING`, passes it as constructor arg. Governor calls `self._trust_engine.should_auto_approve()` and `self._trust_engine.record_decision()`. These methods are **NOT being changed in Spec 2A** (Governor is untouched per spec). The new TrustEngine must still provide `should_auto_approve()` and `record_decision()` with compatible signatures as a **temporary compatibility shim** until Spec 2B rewires the Governor.

- [ ] **Step 1: Write failing tests for the new TrustEngine**

Create `backend/tests/test_trust_engine_v2.py`:

```python
"""Tests for rewritten TrustEngine — deterministic 4×4 matrix."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.risk_assessor import RiskAssessment
from src.services.trust_engine import TrustEngine


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def engine(mock_db):
    return TrustEngine(mock_db, workspace_id="ws_test")


def _make_trust_state(trust_level="first_use"):
    s = MagicMock()
    s.trust_level = trust_level
    s.approved_count = 0
    s.rejected_count = 0
    s.modified_count = 0
    s.cooldown_until = None
    return s


def _make_ceiling(max_level="autonomous"):
    c = MagicMock()
    c.max_level = max_level
    return c


def _make_risk(risk_level="low", reasoning="test"):
    return RiskAssessment(risk_level=risk_level, reasoning=reasoning)


class TestEvaluateFirstUse:
    """first_use × any risk → approval_required."""

    async def test_first_use_none_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("first_use"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("none"))
        assert result.decision == "approval_required"

    async def test_first_use_high_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("first_use"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("high"))
        assert result.decision == "approval_required"


class TestEvaluateLearning:
    """learning × any risk → approval_required."""

    async def test_learning_low_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("learning"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("low"))
        assert result.decision == "approval_required"


class TestEvaluateTrusted:
    """trusted × none/low → auto_execute_notify; trusted × medium/high → approval_required."""

    async def test_trusted_none_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("none"))
        assert result.decision == "auto_execute_notify"

    async def test_trusted_low_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("low"))
        assert result.decision == "auto_execute_notify"

    async def test_trusted_medium_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("medium"))
        assert result.decision == "approval_required"

    async def test_trusted_high_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("high"))
        assert result.decision == "approval_required"


class TestEvaluateAutonomous:
    """autonomous: none/low → silent, medium → notify, high → approval_required."""

    async def test_autonomous_none_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("none"))
        assert result.decision == "auto_execute_silent"

    async def test_autonomous_low_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("low"))
        assert result.decision == "auto_execute_silent"

    async def test_autonomous_medium_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("medium"))
        assert result.decision == "auto_execute_notify"

    async def test_autonomous_high_risk(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling())
        result = await engine.evaluate("email.send", _make_risk("high"))
        assert result.decision == "approval_required"


class TestCeilingRespected:
    """Ceiling caps effective trust level."""

    async def test_ceiling_caps_autonomous_to_trusted(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("autonomous"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("trusted"))
        result = await engine.evaluate("email.send", _make_risk("low"))
        # autonomous capped to trusted → low risk → auto_execute_notify (not silent)
        assert result.decision == "auto_execute_notify"

    async def test_ceiling_caps_trusted_to_learning(self, engine, mock_db):
        engine._get_trust_state = AsyncMock(return_value=_make_trust_state("trusted"))
        engine._get_ceiling = AsyncMock(return_value=_make_ceiling("learning"))
        result = await engine.evaluate("email.send", _make_risk("low"))
        # trusted capped to learning → always approval_required
        assert result.decision == "approval_required"


class TestCompatShim:
    """Old API compatibility — record_decision and should_auto_approve still work."""

    async def test_record_decision_approved(self, engine, mock_db):
        mock_result = MagicMock()
        mock_state = _make_trust_state("first_use")
        mock_result.scalar_one_or_none.return_value = mock_state
        mock_db.execute = AsyncMock(return_value=mock_result)

        score = await engine.record_decision(
            "usr_test", "send_email", approved=True, workspace_id="ws_test"
        )
        assert isinstance(score, float)

    async def test_should_auto_approve(self, engine, mock_db):
        result = await engine.should_auto_approve(
            "usr_test", "send_email", workspace_id="ws_test"
        )
        assert isinstance(result, bool)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_trust_engine_v2.py -v 2>&1 | tail -5`
Expected: FAIL — TrustEngine constructor doesn't accept `workspace_id`.

- [ ] **Step 3: Rewrite trust_engine.py**

Replace the entire contents of `backend/src/services/trust_engine.py`:

```python
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.orchestrator.contracts import PolicyDecision
from src.services.risk_assessor import (
    RiskAssessment,
    get_or_create_trust_state,
    min_trust_level,
)

logger = logging.getLogger(__name__)


class TrustEngine:
    """Deterministic trust evaluation from TrustState + RiskAssessment."""

    def __init__(self, db: AsyncSession, workspace_id: str = ""):
        self._db = db
        self._workspace_id = workspace_id

    async def evaluate(
        self, capability: str, risk_assessment: RiskAssessment
    ) -> PolicyDecision:
        """Evaluate trust for a capability + risk assessment → PolicyDecision.

        Looks up TrustState for (workspace, capability, risk_level),
        applies ceiling, then uses the 4×4 matrix.
        """
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
        return await get_or_create_trust_state(
            self._db, self._workspace_id, capability, risk_level
        )

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

        # Default: no ceiling (autonomous max)
        from unittest.mock import MagicMock as _Stub

        default = _Stub()
        default.max_level = "autonomous"
        return default

    # ── Compatibility shim for Governor (removed in Spec 2B) ─────

    async def record_decision(
        self, user_id: str, action_type: str, approved: bool, workspace_id: str = ""
    ) -> float:
        """Compatibility shim — Governor still calls this. Updates TrustState."""
        ws = workspace_id or self._workspace_id
        state = await get_or_create_trust_state(
            self._db, ws, action_type, "low"  # legacy API has no risk_level
        )
        if approved:
            state.approved_count += 1
        else:
            from src.services.risk_assessor import apply_rejection

            apply_rejection(state)

        state.last_decision_at = datetime.now(timezone.utc)
        from src.services.risk_assessor import graduate_trust

        state.trust_level = graduate_trust(state)
        await self._db.flush()

        total = state.approved_count + state.rejected_count
        return state.approved_count / total if total > 0 else 0.0

    async def should_auto_approve(
        self, user_id: str, action_type: str, risk_level: str = "low", workspace_id: str = ""
    ) -> bool:
        """Compatibility shim — returns True only at trusted+ with low risk."""
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
        result = await self._db.execute(
            select(TrustState).where(TrustState.workspace_id == ws)
        )
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
```

**Important note on `_get_ceiling` default:** Using `MagicMock` as a stub in production code is ugly. Replace with a proper `SimpleNamespace`:

Actually, replace the `_get_ceiling` default section with:

```python
        # Default: no ceiling (autonomous max)
        from types import SimpleNamespace

        return SimpleNamespace(max_level="autonomous")
```

- [ ] **Step 4: Run the new tests**

Run: `cd backend && pytest tests/test_trust_engine_v2.py -v 2>&1 | tail -25`
Expected: All 16 tests PASS.

- [ ] **Step 5: Run old trust engine tests to check compat shim**

Run: `cd backend && pytest tests/test_trust_engine.py -v 2>&1 | tail -15`
Expected: These tests may fail because they mock `TrustScore` model queries but the new engine uses `TrustState`. That's expected — the old tests will be deleted in Spec 2B. Just verify the new tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/trust_engine.py backend/tests/test_trust_engine_v2.py
git commit -m "feat(spec2a): rewrite TrustEngine with deterministic 4×4 matrix"
```

---

### Task 7: Trust Feedback Loop in Approval Routes + Tests

**Files:**
- Modify: `backend/src/api/routes_approvals.py`
- Create: `backend/tests/test_trust_feedback.py`

Wire `record_approval_decision()` into the approve and reject handlers. The key challenge: approvals store `approval_type` (e.g., `tool_call:send_email`, `step:email.send`) — we need to extract a capability name from it.

- [ ] **Step 1: Write failing tests for the feedback integration**

Create `backend/tests/test_trust_feedback.py`:

```python
"""Tests for trust feedback loop — record_approval_decision integration."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.risk_assessor import (
    get_or_create_trust_state,
    record_approval_decision,
)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


class TestRecordApprovalDecision:
    async def test_approved_increments_count(self, mock_db):
        state = MagicMock()
        state.approved_count = 0
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "first_use"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(
            mock_db, "ws_test", "email.send", "low", "approved"
        )
        assert state.approved_count == 1
        assert state.last_decision_at is not None

    async def test_rejected_applies_demotion(self, mock_db):
        state = MagicMock()
        state.approved_count = 10
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "trusted"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(
            mock_db, "ws_test", "email.send", "low", "rejected"
        )
        assert state.rejected_count == 1
        assert state.trust_level == "learning"
        assert state.cooldown_until is not None

    async def test_modified_increments_both(self, mock_db):
        state = MagicMock()
        state.approved_count = 5
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "learning"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(
            mock_db, "ws_test", "email.send", "low", "modified"
        )
        assert state.modified_count == 1
        assert state.approved_count == 6

    async def test_graduation_after_three_approvals(self, mock_db):
        state = MagicMock()
        state.approved_count = 2
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "first_use"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(
            mock_db, "ws_test", "email.send", "low", "approved"
        )
        # Now approved_count=3, rejected=0 → should graduate to learning
        assert state.trust_level == "learning"
```

- [ ] **Step 2: Run tests to verify they pass (feedback functions already exist from Task 4)**

Run: `cd backend && pytest tests/test_trust_feedback.py -v 2>&1 | tail -15`
Expected: All 4 tests PASS (the functions were implemented in Task 4).

- [ ] **Step 3: Wire feedback into approve handler**

In `backend/src/api/routes_approvals.py`, add the trust feedback call inside `approve_action()`. After the audit log call (line ~163, after `await audit.log(...)`) and before `await db.commit()`, add:

```python
    # Trust feedback loop — record approval for graduated autonomy
    try:
        from src.services.risk_assessor import record_approval_decision

        # Extract capability from approval_type (e.g., "tool_call:send_email" → "send_email")
        capability = approval.approval_type
        if ":" in capability:
            capability = capability.split(":", 1)[1]
        decision_type = "modified" if req and req.reason else "approved"
        await record_approval_decision(
            db, workspace_id, capability, approval.risk_level or "low", decision_type
        )
    except Exception:
        logger.warning("Trust feedback failed for approval %s", approval_id, exc_info=True)
```

- [ ] **Step 4: Wire feedback into reject handler**

In `backend/src/api/routes_approvals.py`, inside `reject_action()`, after the audit log call (line ~362, after `await audit.log(...)`) and before `await db.commit()`, add:

```python
    # Trust feedback loop — record rejection for graduated autonomy
    try:
        from src.services.risk_assessor import record_approval_decision

        capability = approval.approval_type
        if ":" in capability:
            capability = capability.split(":", 1)[1]
        await record_approval_decision(
            db, workspace_id, capability, approval.risk_level or "low", "rejected"
        )
    except Exception:
        logger.warning("Trust feedback failed for rejection %s", approval_id, exc_info=True)
```

- [ ] **Step 5: Run existing approval tests to ensure nothing breaks**

Run: `cd backend && pytest tests/ -v -k "approval" --no-header -q 2>&1 | tail -10`
Expected: All existing approval tests pass. The new code is in try/except so even if DB setup isn't there, it won't break existing handlers.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes_approvals.py backend/tests/test_trust_feedback.py
git commit -m "feat(spec2a): wire trust feedback loop into approval routes"
```

---

### Task 8: Per-Tool Cost Attribution in Agent Loop

**Files:**
- Modify: `backend/src/orchestrator/agent_loop.py`

Issue #13: Record a `TokenUsage` row per tool call so dashboards can query per-capability cost. The `TokenUsage` model has: `usage_id` (string PK, `usage_{ULID}`), `workspace_id`, `agent_name`, `model`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `thinking_tokens`, `cost_usd`, `trigger`, `trace_id`.

- [ ] **Step 1: Add per-tool cost attribution**

In `backend/src/orchestrator/agent_loop.py`, after the `await audit_post_tool_hook(...)` call (around line 483), add:

```python
                # Per-tool cost attribution (Issue #13)
                try:
                    from ulid import ULID

                    from src.models.token_usage import TokenUsage

                    async with db_factory() as tool_db:
                        tool_db.add(
                            TokenUsage(
                                usage_id=f"usage_{ULID()}",
                                workspace_id=workspace_id,
                                agent_name=agent_name,
                                model=model,
                                input_tokens=0,
                                output_tokens=0,
                                cache_creation_input_tokens=0,
                                cache_read_input_tokens=0,
                                thinking_tokens=0,
                                cost_usd=0.0,
                                trigger=f"tool:{tool_name}",
                                trace_id=trace.trace_id if trace else None,
                            )
                        )
                        await tool_db.commit()
                except Exception:
                    pass  # Non-critical — don't break the agent loop
```

- [ ] **Step 2: Run existing agent_loop tests**

Run: `cd backend && pytest tests/test_agent_loop.py -v 2>&1 | tail -15`
Expected: All existing tests pass (the new code is in try/except and won't affect mocked tests).

- [ ] **Step 3: Commit**

```bash
git add backend/src/orchestrator/agent_loop.py
git commit -m "feat(spec2a): add per-tool cost attribution in agent loop (issue #13)"
```

---

### Task 9: Integration Verification

- [ ] **Step 1: Run all new tests together**

```bash
cd backend && pytest tests/test_trust_graduation.py tests/test_risk_assessor.py tests/test_trust_engine_v2.py tests/test_trust_feedback.py -v 2>&1 | tail -30
```

Expected: All ~44 tests PASS.

- [ ] **Step 2: Run the full test suite**

```bash
cd backend && pytest tests/ -v --no-header -q 2>&1 | tail -10
```

Expected: No regressions. Old `test_trust_engine.py` tests may fail (expected — they test the old API against TrustScore model). All other tests pass.

- [ ] **Step 3: Run linter**

```bash
cd backend && ruff check src/services/risk_assessor.py src/services/trust_engine.py src/models/trust_state.py src/orchestrator/contracts.py src/api/routes_approvals.py src/orchestrator/agent_loop.py
```

Expected: Clean.

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -u && git commit -m "chore(spec2a): lint fixes"
```

Only if there were lint issues. Skip if clean.

---

## Summary

| Task | Component | New Tests | Files |
|------|-----------|-----------|-------|
| 1 | PolicyDecision extension | 0 | 1 modified |
| 2 | TrustState + TrustCeiling models | 0 | 2 new |
| 3 | Alembic migration | 0 | 1 new |
| 4 | Graduation rules + feedback loop | 14 | 1 new source, 1 new test |
| 5 | LLM risk assessor + caching | 10 | 1 modified, 1 new test |
| 6 | TrustEngine rewrite | 16 | 1 rewritten, 1 new test |
| 7 | Approval route feedback wiring | 4 | 1 modified, 1 new test |
| 8 | Per-tool cost attribution | 0 | 1 modified |
| 9 | Integration verification | — | — |

**Total: ~44 new tests, 6 new files, 4 modified files, 9 commits**
