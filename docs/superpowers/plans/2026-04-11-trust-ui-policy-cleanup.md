# Spec 2B-ii: Trust UI + Policy Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete dead approval systems, absorb policy modes into trust ceilings, build trust API endpoints, add frontend Trust tab, and enrich approval surfaces with trust context.

**Architecture:** 6 components executed bottom-up: (1) delete dead code + migration, (2) policy mode → ceiling mapping in settings endpoint, (3) time-based policy absorption into TrustEngine, (4) 6 new trust API endpoints, (5) frontend Trust tab in Settings, (6) trust context in approval surfaces. Each component is independently testable.

**Tech Stack:** Python 3.12 (async), FastAPI, SQLAlchemy, Alembic, Pydantic; Next.js, TypeScript, Tailwind CSS, Zustand.

---

## File Structure

### Deleted Files (3)
- `backend/src/services/approval_policy_engine.py` — dead, zero callers
- `backend/src/models/trust_score.py` — replaced by TrustState
- `backend/src/models/approval_policy.py` — replaced by TrustCeiling

### New Files (3)
- `backend/alembic/versions/056_drop_approval_policies_and_trust_scores.py` — drop 2 tables
- `backend/src/api/routes_trust.py` — 6 trust endpoints
- `backend/tests/test_trust_api.py` — tests for trust API + policy absorption

### Modified Files — Backend (5)
- `backend/src/models/__init__.py` — remove TrustScore, ApprovalPolicy imports/exports
- `backend/src/api/app.py` — register trust router
- `backend/src/api/routes_settings.py` — policy mode → trust ceiling batch update
- `backend/src/services/trust_engine.py` — add `get_trust_dashboard_grouped()`, `set_ceiling()`, `reset_trust_for_capability()`, time-scoped ceiling methods; remove compat shims
- `backend/src/services/surface_builder.py` — trust context in approval surface preview

### Modified Files — Frontend (4)
- `frontend/src/lib/types.ts` — TrustDashboardEntry, TrustCapabilityDetail, TimePolicyRule types
- `frontend/src/lib/api.ts` — 6 trust API functions
- `frontend/src/app/settings/page.tsx` — Trust tab with capability groups, progress bars, ceiling controls
- `frontend/src/stores/activity-store.ts` — `auto_execute_notify` event type

---

## Task 1: Delete Dead Systems + Alembic Migration

**Files:**
- Delete: `backend/src/services/approval_policy_engine.py`
- Delete: `backend/src/models/trust_score.py`
- Delete: `backend/src/models/approval_policy.py`
- Modify: `backend/src/models/__init__.py`
- Create: `backend/alembic/versions/056_drop_approval_policies_and_trust_scores.py`
- Test: `backend/tests/test_trust_api.py` (deletion verification)

- [ ] **Step 1: Write deletion verification test**

```python
# backend/tests/test_trust_api.py
"""Tests for trust API, policy absorption, and dead code deletion."""

import importlib


def test_approval_policy_engine_deleted():
    """ApprovalPolicyEngine must not be importable."""
    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("src.services.approval_policy_engine")


def test_trust_score_model_deleted():
    """TrustScore model must not be importable from models."""
    with __import__("pytest").raises(ImportError):
        from src.models.trust_score import TrustScore  # noqa: F401


def test_approval_policy_model_deleted():
    """ApprovalPolicy model must not be importable from models."""
    with __import__("pytest").raises(ImportError):
        from src.models.approval_policy import ApprovalPolicy  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_trust_api.py::test_approval_policy_engine_deleted tests/test_trust_api.py::test_trust_score_model_deleted tests/test_trust_api.py::test_approval_policy_model_deleted -v`
Expected: FAIL — modules still exist.

- [ ] **Step 3: Delete the 3 dead files**

```bash
cd backend
rm src/services/approval_policy_engine.py
rm src/models/trust_score.py
rm src/models/approval_policy.py
```

- [ ] **Step 4: Remove imports from models/__init__.py**

In `backend/src/models/__init__.py`, remove these lines:

```python
# REMOVE this import:
from src.models.approval_policy import ApprovalPolicy
# REMOVE this import:
from src.models.trust_score import TrustScore
```

And remove from `__all__`:
```python
# REMOVE these entries:
"TrustScore",
"ApprovalPolicy",
```

The resulting trust section in `__all__` should be:
```python
    # Trust
    "TrustState",
    "TrustCeiling",
```

- [ ] **Step 5: Create Alembic migration to drop tables**

```python
# backend/alembic/versions/056_drop_approval_policies_and_trust_scores.py
"""Drop approval_policies and trust_scores tables.

Revision ID: 056
Revises: 055
"""

from alembic import op

revision = "056"
down_revision = "055"


def upgrade() -> None:
    op.drop_index("ix_approval_policies_ws", table_name="approval_policies", if_exists=True)
    op.drop_index("ix_approval_policies_ws_cap", table_name="approval_policies", if_exists=True)
    op.drop_table("approval_policies")

    op.drop_index("ix_trust_scores_unique", table_name="trust_scores", if_exists=True)
    op.drop_table("trust_scores")


def downgrade() -> None:
    # These tables are dead code — no downgrade needed.
    # If you need them back, recreate from git history.
    pass
```

- [ ] **Step 6: Run deletion tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_trust_api.py -v -k "deleted"`
Expected: 3 PASS

- [ ] **Step 7: Grep to confirm zero remaining references**

```bash
cd backend && grep -rn "ApprovalPolicyEngine\|from src.models.trust_score\|from src.models.approval_policy" src/ --include="*.py" | grep -v __pycache__
```
Expected: zero results (specs/docs are outside `src/`).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(spec2b-ii): delete ApprovalPolicyEngine, TrustScore, ApprovalPolicy

Drop 3 dead files, remove from models __init__, add Alembic migration
to drop approval_policies + trust_scores tables."
```

---

## Task 2: Trust API Endpoints + TrustEngine Enhancements

**Files:**
- Create: `backend/src/api/routes_trust.py`
- Modify: `backend/src/services/trust_engine.py`
- Modify: `backend/src/api/app.py`
- Test: `backend/tests/test_trust_api.py`

- [ ] **Step 1: Write tests for trust API endpoints**

Append to `backend/tests/test_trust_api.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from datetime import datetime, timezone


def _make_trust_state(
    capability="email.send",
    risk_level="low",
    trust_level="learning",
    approved_count=5,
    rejected_count=0,
    modified_count=0,
    last_decision_at=None,
    cooldown_until=None,
    workspace_id="ws_test",
):
    return SimpleNamespace(
        capability=capability,
        risk_level=risk_level,
        trust_level=trust_level,
        approved_count=approved_count,
        rejected_count=rejected_count,
        modified_count=modified_count,
        last_decision_at=last_decision_at or datetime.now(timezone.utc),
        cooldown_until=cooldown_until,
        workspace_id=workspace_id,
    )


def _make_ceiling(capability="email.send", max_level="trusted"):
    return SimpleNamespace(capability=capability, max_level=max_level)


@pytest.mark.asyncio
async def test_get_trust_dashboard_grouped():
    """Dashboard returns capabilities grouped by family."""
    from src.services.trust_engine import TrustEngine

    mock_db = AsyncMock()
    engine = TrustEngine(mock_db, workspace_id="ws_test")

    states = [
        _make_trust_state("email.send", "low", "learning", 5, 0),
        _make_trust_state("email.send", "medium", "first_use", 1, 0),
        _make_trust_state("email.read", "low", "trusted", 12, 0),
        _make_trust_state("calendar.create", "medium", "first_use", 0, 0),
    ]
    ceilings = [_make_ceiling("email.send", "trusted")]

    mock_result_states = MagicMock()
    mock_result_states.scalars.return_value.all.return_value = states
    mock_result_ceilings = MagicMock()
    mock_result_ceilings.scalars.return_value.all.return_value = ceilings

    mock_db.execute = AsyncMock(side_effect=[mock_result_states, mock_result_ceilings])

    dashboard = await engine.get_trust_dashboard_grouped()

    assert isinstance(dashboard, list)
    # Should have entries for email.send, email.read, calendar.create
    caps = {e["capability"] for e in dashboard}
    assert "email.send" in caps
    assert "email.read" in caps
    assert "calendar.create" in caps

    # email.send should have ceiling info
    email_send = next(e for e in dashboard if e["capability"] == "email.send")
    assert email_send["ceiling"] == "trusted"
    assert len(email_send["risk_levels"]) == 2


@pytest.mark.asyncio
async def test_get_capability_detail():
    """Capability detail returns per-risk breakdown with graduation progress."""
    from src.services.trust_engine import TrustEngine

    mock_db = AsyncMock()
    engine = TrustEngine(mock_db, workspace_id="ws_test")

    states = [
        _make_trust_state("email.send", "low", "learning", 5, 0),
        _make_trust_state("email.send", "medium", "first_use", 1, 0),
        _make_trust_state("email.send", "high", "first_use", 0, 0),
    ]
    ceiling = _make_ceiling("email.send", "trusted")

    mock_result_states = MagicMock()
    mock_result_states.scalars.return_value.all.return_value = states
    mock_result_ceiling = MagicMock()
    mock_result_ceiling.scalar_one_or_none.return_value = ceiling

    mock_db.execute = AsyncMock(side_effect=[mock_result_states, mock_result_ceiling])

    detail = await engine.get_capability_detail("email.send")

    assert detail["capability"] == "email.send"
    assert detail["ceiling"] == "trusted"
    assert len(detail["risk_levels"]) == 3
    # Check graduation_progress for the learning entry
    low_entry = next(r for r in detail["risk_levels"] if r["risk_level"] == "low")
    assert "graduation_progress" in low_entry


@pytest.mark.asyncio
async def test_set_ceiling():
    """set_ceiling upserts a TrustCeiling record."""
    from src.services.trust_engine import TrustEngine

    mock_db = AsyncMock()
    engine = TrustEngine(mock_db, workspace_id="ws_test")

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    await engine.set_ceiling("email.send", "trusted")

    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.capability == "email.send"
    assert added.max_level == "trusted"


@pytest.mark.asyncio
async def test_reset_trust_for_capability():
    """reset_trust resets all risk-level states for a capability."""
    from src.services.trust_engine import TrustEngine

    mock_db = AsyncMock()
    engine = TrustEngine(mock_db, workspace_id="ws_test")

    state1 = _make_trust_state("email.send", "low", "trusted", 15, 1)
    state2 = _make_trust_state("email.send", "medium", "learning", 5, 0)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [state1, state2]
    mock_db.execute = AsyncMock(return_value=mock_result)

    await engine.reset_trust_for_capability("email.send")

    assert state1.trust_level == "first_use"
    assert state1.approved_count == 0
    assert state2.trust_level == "first_use"
    assert state2.approved_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_trust_api.py -v -k "dashboard_grouped or capability_detail or set_ceiling or reset_trust_for"`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Add new methods to TrustEngine**

Replace the compatibility shim section in `backend/src/services/trust_engine.py` (lines 94-178) with these real methods:

```python
    # ── Dashboard + Detail Methods ──────────────────────────────

    async def get_trust_dashboard_grouped(self) -> list[dict]:
        """All capabilities with trust levels, progress, and ceilings.

        Returns list of dicts, each with:
        - capability, family, ceiling, risk_levels (list of per-risk entries)
        """
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

        # Group states by capability
        by_cap: dict[str, list] = {}
        for s in states:
            by_cap.setdefault(s.capability, []).append(s)

        entries = []
        for cap, cap_states in by_cap.items():
            meta = CAPABILITY_CATALOG.get(cap)
            family = meta.family if meta else "unknown"

            # Best trust level across risk levels
            best_level = "first_use"
            for s in cap_states:
                if _trust_level_index(s.trust_level) > _trust_level_index(best_level):
                    best_level = s.trust_level

            risk_levels = []
            for s in cap_states:
                risk_levels.append({
                    "risk_level": s.risk_level,
                    "trust_level": s.trust_level,
                    "approved_count": s.approved_count,
                    "rejected_count": s.rejected_count,
                    "graduation_progress": _graduation_progress(s),
                })

            entries.append({
                "capability": cap,
                "family": str(family),
                "trust_level": best_level,
                "ceiling": ceilings.get(cap, "autonomous"),
                "risk_levels": risk_levels,
            })

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
            risk_levels.append({
                "risk_level": s.risk_level,
                "trust_level": s.trust_level,
                "approved_count": s.approved_count,
                "rejected_count": s.rejected_count,
                "modified_count": s.modified_count,
                "last_decision_at": (
                    s.last_decision_at.isoformat() if s.last_decision_at else None
                ),
                "cooldown_until": (
                    s.cooldown_until.isoformat() if s.cooldown_until else None
                ),
                "graduation_progress": _graduation_progress(s),
            })

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

    async def set_ceilings_batch(
        self, capabilities: list[str], max_level: str
    ) -> int:
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
        # Time policies stored per-workspace in settings
        # We read from workspace owner's settings
        policies = await svc.get_global(
            self._workspace_id, "trust", "time_policies"
        )
        if not policies or not isinstance(policies, list):
            return []
        return policies

    async def set_time_policies(self, policies: list[dict]) -> None:
        """Set time-scoped ceiling overrides for this workspace."""
        from src.services.settings_service import SettingsService

        svc = SettingsService(self._db)
        await svc.set_global(
            self._workspace_id, "trust", "time_policies", policies
        )
```

Also add this module-level helper function after the imports (before the class):

```python
def _trust_level_index(level: str) -> int:
    """Index of trust level for ordering comparisons."""
    _LEVELS = ("first_use", "learning", "trusted", "autonomous")
    try:
        return _LEVELS.index(level)
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
        # Need 3 approved, 0 rejected → learning
        return {
            "next_level": "learning",
            "current": approved,
            "target": 3,
            "percentage": min(approved / 3, 1.0) if approved < 3 else 1.0,
            "blocked_by_rejections": rejected > 0,
        }
    elif level == "learning":
        # Need 10 approved, <10% rejection → trusted
        return {
            "next_level": "trusted",
            "current": approved,
            "target": 10,
            "percentage": min(approved / 10, 1.0),
            "blocked_by_rejections": (
                total > 0 and rejected / total >= 0.10
            ),
        }
    elif level == "trusted":
        # Need 25 approved, <5% rejection → autonomous
        return {
            "next_level": "autonomous",
            "current": approved,
            "target": 25,
            "percentage": min(approved / 25, 1.0),
            "blocked_by_rejections": (
                total > 0 and rejected / total >= 0.05
            ),
        }
    else:
        # autonomous — fully graduated
        return {
            "next_level": None,
            "current": approved,
            "target": approved,
            "percentage": 1.0,
            "blocked_by_rejections": False,
        }
```

- [ ] **Step 4: Run TrustEngine tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_trust_api.py -v -k "dashboard_grouped or capability_detail or set_ceiling or reset_trust_for"`
Expected: 4 PASS

- [ ] **Step 5: Write tests for API routes**

Append to `backend/tests/test_trust_api.py`:

```python
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_user():
    return SimpleNamespace(user_id="usr_test", email="test@test.com")


@pytest.mark.asyncio
async def test_trust_dashboard_endpoint(mock_db, mock_user):
    """GET /v1/trust/dashboard returns grouped capabilities."""
    from src.api.app import create_app
    from src.api.deps import get_current_user, get_current_workspace_id, get_session

    app = create_app()
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_workspace_id] = lambda: "ws_test"

    # Mock TrustEngine.get_trust_dashboard_grouped
    with patch(
        "src.api.routes_trust.TrustEngine"
    ) as MockEngine:
        instance = MockEngine.return_value
        instance.get_trust_dashboard_grouped = AsyncMock(
            return_value=[
                {
                    "capability": "email.send",
                    "family": "email",
                    "trust_level": "learning",
                    "ceiling": "autonomous",
                    "risk_levels": [],
                }
            ]
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/v1/trust/dashboard")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["capabilities"]) == 1
        assert data["capabilities"][0]["capability"] == "email.send"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_set_ceiling_endpoint(mock_db, mock_user):
    """PUT /v1/trust/{capability}/ceiling sets max trust level."""
    from src.api.app import create_app
    from src.api.deps import get_current_user, get_current_workspace_id, get_session

    app = create_app()
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_workspace_id] = lambda: "ws_test"

    with patch(
        "src.api.routes_trust.TrustEngine"
    ) as MockEngine:
        instance = MockEngine.return_value
        instance.set_ceiling = AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/v1/trust/email.send/ceiling",
                json={"max_level": "trusted"},
            )

        assert resp.status_code == 200
        instance.set_ceiling.assert_called_once_with("email.send", "trusted")

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reset_trust_endpoint(mock_db, mock_user):
    """POST /v1/trust/{capability}/reset resets trust scores."""
    from src.api.app import create_app
    from src.api.deps import get_current_user, get_current_workspace_id, get_session

    app = create_app()
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_workspace_id] = lambda: "ws_test"

    with patch(
        "src.api.routes_trust.TrustEngine"
    ) as MockEngine:
        instance = MockEngine.return_value
        instance.reset_trust_for_capability = AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/v1/trust/email.send/reset")

        assert resp.status_code == 200
        instance.reset_trust_for_capability.assert_called_once_with("email.send")

    app.dependency_overrides.clear()
```

- [ ] **Step 6: Create routes_trust.py**

```python
# backend/src/api/routes_trust.py
"""Trust management API — dashboard, ceiling controls, and time policies."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_current_workspace_id, get_session
from src.models.users import User
from src.services.trust_engine import TrustEngine

router = APIRouter()
logger = logging.getLogger(__name__)

VALID_TRUST_LEVELS = {"first_use", "learning", "trusted", "autonomous", "blocked"}


# ── Request/Response Models ─────────────────────────────────────


class TrustRiskLevel(BaseModel):
    risk_level: str
    trust_level: str
    approved_count: int
    rejected_count: int
    graduation_progress: dict


class TrustCapabilityEntry(BaseModel):
    capability: str
    family: str
    trust_level: str
    ceiling: str
    risk_levels: list[TrustRiskLevel]


class TrustDashboardResponse(BaseModel):
    capabilities: list[TrustCapabilityEntry]


class TrustCapabilityDetailRisk(BaseModel):
    risk_level: str
    trust_level: str
    approved_count: int
    rejected_count: int
    modified_count: int
    last_decision_at: str | None
    cooldown_until: str | None
    graduation_progress: dict


class TrustCapabilityDetailResponse(BaseModel):
    capability: str
    family: str
    ceiling: str
    risk_levels: list[TrustCapabilityDetailRisk]


class CeilingRequest(BaseModel):
    max_level: str


class CeilingResponse(BaseModel):
    capability: str
    max_level: str


class ResetResponse(BaseModel):
    capability: str
    status: str


class TimePolicyRule(BaseModel):
    start_hour: int
    end_hour: int
    max_level: str
    days: list[int] | None = None


class TimePoliciesResponse(BaseModel):
    policies: list[TimePolicyRule]


class TimePoliciesRequest(BaseModel):
    policies: list[TimePolicyRule]


# ── Endpoints ───────────────────────────────────────────────────


@router.get("/v1/trust/dashboard", response_model=TrustDashboardResponse)
async def get_trust_dashboard(
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """All capabilities with trust levels, graduation progress, and ceilings."""
    engine = TrustEngine(db, workspace_id=workspace_id)
    entries = await engine.get_trust_dashboard_grouped()
    return TrustDashboardResponse(capabilities=entries)


@router.get(
    "/v1/trust/{capability:path}",
    response_model=TrustCapabilityDetailResponse,
)
async def get_trust_capability(
    capability: str,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Detailed trust state across risk levels for one capability."""
    engine = TrustEngine(db, workspace_id=workspace_id)
    detail = await engine.get_capability_detail(capability)
    return TrustCapabilityDetailResponse(**detail)


@router.put(
    "/v1/trust/{capability:path}/ceiling",
    response_model=CeilingResponse,
)
async def set_trust_ceiling(
    capability: str,
    req: CeilingRequest,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Set the maximum trust level for a capability."""
    if req.max_level not in VALID_TRUST_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid level. Must be one of: {VALID_TRUST_LEVELS}",
        )

    engine = TrustEngine(db, workspace_id=workspace_id)
    await engine.set_ceiling(capability, req.max_level)
    await db.commit()
    return CeilingResponse(capability=capability, max_level=req.max_level)


@router.post(
    "/v1/trust/{capability:path}/reset",
    response_model=ResetResponse,
)
async def reset_trust(
    capability: str,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Reset trust scores for a capability back to first_use."""
    engine = TrustEngine(db, workspace_id=workspace_id)
    await engine.reset_trust_for_capability(capability)
    await db.commit()
    return ResetResponse(capability=capability, status="reset")


@router.get("/v1/trust-time-policies", response_model=TimePoliciesResponse)
async def get_time_policies(
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get time-based trust ceiling overrides."""
    engine = TrustEngine(db, workspace_id=workspace_id)
    policies = await engine.get_time_policies()
    return TimePoliciesResponse(policies=policies)


@router.put("/v1/trust-time-policies", response_model=TimePoliciesResponse)
async def set_time_policies(
    req: TimePoliciesRequest,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Set time-based trust ceiling overrides."""
    for p in req.policies:
        if p.max_level not in VALID_TRUST_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid level '{p.max_level}'. Must be one of: {VALID_TRUST_LEVELS}",
            )
        if not (0 <= p.start_hour <= 23 and 0 <= p.end_hour <= 23):
            raise HTTPException(status_code=400, detail="Hours must be 0-23")

    engine = TrustEngine(db, workspace_id=workspace_id)
    await engine.set_time_policies(
        [p.model_dump() for p in req.policies]
    )
    await db.commit()
    return TimePoliciesResponse(policies=req.policies)
```

**Note on path parameters:** The capability strings contain dots (e.g., `email.send`), so we use `{capability:path}` to capture the full string. The time-policy endpoints use `/v1/trust-time-policies` (hyphenated) to avoid path conflicts with `{capability:path}`.

- [ ] **Step 7: Register trust router in app.py**

Add to `backend/src/api/app.py` — import near line 32:

```python
from src.api.routes_trust import router as trust_router
```

Add router registration after the settings router (after line 303):

```python
    # Trust management
    app.include_router(trust_router, tags=["trust"])
```

- [ ] **Step 8: Run API endpoint tests**

Run: `cd backend && python -m pytest tests/test_trust_api.py -v -k "endpoint"`
Expected: 3 PASS

- [ ] **Step 9: Commit**

```bash
git add backend/src/api/routes_trust.py backend/src/services/trust_engine.py backend/src/api/app.py backend/tests/test_trust_api.py
git commit -m "feat(spec2b-ii): trust API endpoints + TrustEngine dashboard/ceiling methods

6 new endpoints: dashboard, capability detail, set ceiling, reset,
get/set time policies. TrustEngine enhanced with grouped dashboard,
graduation progress, and ceiling management."
```

---

## Task 3: Policy Mode Absorption into Trust Ceilings

**Files:**
- Modify: `backend/src/api/routes_settings.py`
- Test: `backend/tests/test_trust_api.py`

- [ ] **Step 1: Write test for policy mode → ceiling mapping**

Append to `backend/tests/test_trust_api.py`:

```python
@pytest.mark.asyncio
async def test_policy_mode_sets_trust_ceilings(mock_db, mock_user):
    """PUT /v1/settings/policy/mode batch-updates trust ceilings."""
    from src.api.app import create_app
    from src.api.deps import get_current_user, get_current_workspace_id, get_session

    app = create_app()
    app.dependency_overrides[get_session] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_workspace_id] = lambda: "ws_test"

    with patch(
        "src.api.routes_settings.TrustEngine"
    ) as MockEngine:
        instance = MockEngine.return_value
        instance.set_ceilings_batch = AsyncMock(return_value=5)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/v1/settings/policy/mode",
                json={"mode": "lockdown"},
            )

        assert resp.status_code == 200
        # Should have called set_ceilings_batch with "blocked" level
        instance.set_ceilings_batch.assert_called_once()
        call_args = instance.set_ceilings_batch.call_args
        assert call_args[0][1] == "blocked"  # max_level for lockdown

    app.dependency_overrides.clear()


def test_policy_mode_to_ceiling_mapping():
    """Verify the 4 mode → ceiling mappings are correct."""
    from src.api.routes_settings import POLICY_MODE_TO_CEILING

    assert POLICY_MODE_TO_CEILING["lockdown"] == "blocked"
    assert POLICY_MODE_TO_CEILING["approval_required"] == "learning"
    assert POLICY_MODE_TO_CEILING["suggest_only"] == "first_use"
    assert POLICY_MODE_TO_CEILING["full_auto"] is None  # no ceiling restriction
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_trust_api.py -v -k "policy_mode"`
Expected: FAIL — `POLICY_MODE_TO_CEILING` doesn't exist yet.

- [ ] **Step 3: Update routes_settings.py to map policy mode → trust ceilings**

In `backend/src/api/routes_settings.py`, add after the existing imports (line 11):

```python
from src.api.deps import get_current_workspace_id
from src.services.trust_engine import TrustEngine
from src.integrations.capabilities import CAPABILITY_CATALOG
```

Add the mapping constant after the `PolicyModeRequest` class (after line 22):

```python
# Policy mode → trust ceiling level.
# full_auto = None means remove ceiling restrictions.
POLICY_MODE_TO_CEILING: dict[str, str | None] = {
    "lockdown": "blocked",
    "approval_required": "learning",
    "suggest_only": "first_use",
    "full_auto": None,
}
```

Update the `set_policy_mode` endpoint (replace lines 73-89):

```python
@router.put("/v1/settings/policy/mode", response_model=PolicyResponse)
async def set_policy_mode(
    req: PolicyModeRequest,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Change the global policy mode.

    This batch-updates TrustCeiling records for all known capabilities
    to match the selected policy mode, then persists the mode in settings.
    """
    valid_modes = {"lockdown", "approval_required", "suggest_only", "full_auto"}
    if req.mode not in valid_modes:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of: {valid_modes}")

    # Map policy mode to trust ceilings
    ceiling_level = POLICY_MODE_TO_CEILING[req.mode]
    if ceiling_level is not None:
        all_caps = list(CAPABILITY_CATALOG.keys())
        engine = TrustEngine(db, workspace_id=workspace_id)
        await engine.set_ceilings_batch(all_caps, ceiling_level)

    svc = SettingsService(db)
    await svc.set(user.user_id, "policy", "mode", req.mode)
    await db.commit()
    return PolicyResponse(mode=req.mode)
```

- [ ] **Step 4: Run policy mode tests**

Run: `cd backend && python -m pytest tests/test_trust_api.py -v -k "policy_mode"`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes_settings.py backend/tests/test_trust_api.py
git commit -m "feat(spec2b-ii): policy mode → trust ceiling absorption

PUT /v1/settings/policy/mode now batch-updates TrustCeiling records:
lockdown→blocked, approval_required→learning, suggest_only→first_use,
full_auto→no ceiling. Settings UI unchanged — same 4-mode radio."
```

---

## Task 4: Trust Context in Approval Surfaces

**Files:**
- Modify: `backend/src/services/surface_builder.py`
- Modify: `backend/src/services/surface_detail_builders.py`
- Test: `backend/tests/test_trust_api.py`

- [ ] **Step 1: Write test for trust context in approval preview**

Append to `backend/tests/test_trust_api.py`:

```python
@pytest.mark.asyncio
async def test_approval_surface_includes_trust_context():
    """Approval surface preview should include trust_context metadata."""
    from src.services.surface_builder import SurfaceService

    mock_db = AsyncMock()

    approval = SimpleNamespace(
        approval_id="apr_test1",
        user_id="usr_test",
        title="Send email to Alice",
        summary="Draft reply to Alice about Q3 report",
        risk_level="medium",
        status="pending",
        created_at=datetime.now(timezone.utc),
        artifact_refs={"tool_name": "email.send", "tool_params": {}},
    )

    # Mock approval query
    mock_apr_result = MagicMock()
    mock_apr_result.scalars.return_value.all.return_value = [approval]

    # Mock trust state query
    trust_state = _make_trust_state("email.send", "medium", "first_use", 0, 0)
    mock_trust_result = MagicMock()
    mock_trust_result.scalar_one_or_none.return_value = trust_state

    mock_db.execute = AsyncMock(side_effect=[mock_apr_result, mock_trust_result])

    svc = SurfaceService(mock_db, workspace_id="ws_test")
    surfaces = await svc._build_approval_surfaces("usr_test")

    assert len(surfaces) == 1
    preview = surfaces[0]["preview"]
    # Trust context should be in the preview tags or metrics
    assert any(
        m.get("label") == "Trust" for m in preview.get("metrics", [])
    ) or "trust_context" in surfaces[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_trust_api.py::test_approval_surface_includes_trust_context -v`
Expected: FAIL

- [ ] **Step 3: Update surface_builder.py to add trust context**

In `backend/src/services/surface_builder.py`, add import after line 10:

```python
from src.models.trust_state import TrustState
```

Replace the `_build_approval_surfaces` method (lines 51-92) with:

```python
    async def _build_approval_surfaces(self, user_id: str) -> list[dict[str, Any]]:
        result = await self._db.execute(
            select(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.status == "pending",
            )
            .order_by(Approval.created_at.desc())
            .limit(10)
        )
        approvals = result.scalars().all()
        surfaces: list[dict[str, Any]] = []

        for apr in approvals:
            surface_id = f"approval_{apr.approval_id}"
            risk_level = apr.risk_level or "medium"
            risk_variant = "warning" if risk_level in ("high", "critical") else "default"

            # Resolve trust context from artifact_refs
            trust_context = await self._get_trust_context(apr)

            metrics = [
                SurfaceMetric(label="Risk", value=risk_level, variant=risk_variant),
            ]
            if trust_context:
                metrics.append(
                    SurfaceMetric(
                        label="Trust",
                        value=trust_context["label"],
                        variant=trust_context.get("variant", "default"),
                    )
                )

            preview = SurfacePreview(
                title=apr.title or "Pending Approval",
                subtitle=apr.summary[:120] if apr.summary else None,
                status="awaiting_approval",
                priority="high" if risk_level in ("high", "critical") else "medium",
                metrics=metrics,
            )
            detail_config = build_detail_config("approval", surface_id)

            surfaces.append(
                {
                    "id": surface_id,
                    "kind": "approval",
                    "preview": preview.model_dump(mode="json"),
                    "detail_config": (
                        detail_config.model_dump(mode="json") if detail_config else None
                    ),
                    "created_at": apr.created_at.isoformat() if apr.created_at else None,
                    "trust_context": trust_context,
                }
            )

        return surfaces

    async def _get_trust_context(self, approval) -> dict[str, str] | None:
        """Build trust context dict from approval artifact_refs.

        Returns dict with: trust_level, label, variant, graduation_hint.
        """
        refs = approval.artifact_refs
        if not refs or not isinstance(refs, dict):
            return None

        capability = refs.get("tool_name")
        if not capability:
            return None

        risk_level = approval.risk_level or "low"

        result = await self._db.execute(
            select(TrustState).where(
                TrustState.workspace_id == self._workspace_id,
                TrustState.capability == capability,
                TrustState.risk_level == risk_level,
            )
        )
        state = result.scalar_one_or_none()
        if not state:
            return {
                "trust_level": "first_use",
                "label": "First time",
                "variant": "default",
            }

        level = state.trust_level
        approved = state.approved_count

        if level == "first_use":
            return {
                "trust_level": "first_use",
                "label": "First time",
                "variant": "default",
            }
        elif level == "learning":
            remaining = 10 - approved
            hint = f"{remaining} more to auto-approve" if remaining > 0 else ""
            return {
                "trust_level": "learning",
                "label": f"Similar to {approved} approvals",
                "variant": "default",
                "graduation_hint": hint,
            }

        return {
            "trust_level": level,
            "label": level.replace("_", " ").title(),
            "variant": "success" if level in ("trusted", "autonomous") else "default",
        }
```

- [ ] **Step 4: Update approval detail builder to show trust context**

In `backend/src/services/surface_detail_builders.py`, update the `build_approval_request` function (after the risk badge, before the approve/reject buttons, around line 482):

Add after `children.append(r.badge("apr_risk", ...))` (line 482):

```python
    # Trust context
    if apr.artifact_refs and isinstance(apr.artifact_refs, dict):
        cap = apr.artifact_refs.get("tool_name")
        if cap:
            from src.models.trust_state import TrustState

            trust_result = await db.execute(
                select(TrustState).where(
                    TrustState.workspace_id == apr.workspace_id,
                    TrustState.capability == cap,
                    TrustState.risk_level == (apr.risk_level or "low"),
                )
            )
            trust_state = trust_result.scalar_one_or_none()
            if trust_state:
                level = trust_state.trust_level
                count = trust_state.approved_count
                if level == "first_use":
                    children.append(r.badge("apr_trust", "First time", variant="default"))
                elif level == "learning":
                    remaining = max(0, 10 - count)
                    children.append(
                        r.text(
                            "apr_trust_hint",
                            f"Similar to {count} prior approvals — "
                            f"{remaining} more to auto-approve",
                        )
                    )
                else:
                    children.append(
                        r.badge("apr_trust", level.title(), variant="success")
                    )
            else:
                children.append(r.badge("apr_trust", "First time", variant="default"))
```

- [ ] **Step 5: Run trust context test**

Run: `cd backend && python -m pytest tests/test_trust_api.py::test_approval_surface_includes_trust_context -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/surface_builder.py backend/src/services/surface_detail_builders.py backend/tests/test_trust_api.py
git commit -m "feat(spec2b-ii): trust context in approval surfaces

Approval preview shows trust level (First time / Similar to N approvals).
Detail modal shows graduation hint with count toward auto-approve."
```

---

## Task 5: Frontend Trust Types + API Functions

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add trust types to types.ts**

Append to `frontend/src/lib/types.ts`:

```typescript
// ── Trust ─────────────────────────────────────────────────────

export interface GraduationProgress {
  next_level: string | null;
  current: number;
  target: number;
  percentage: number;
  blocked_by_rejections: boolean;
}

export interface TrustRiskLevel {
  risk_level: string;
  trust_level: string;
  approved_count: number;
  rejected_count: number;
  graduation_progress: GraduationProgress;
}

export interface TrustDashboardEntry {
  capability: string;
  family: string;
  trust_level: string;
  ceiling: string;
  risk_levels: TrustRiskLevel[];
}

export interface TrustCapabilityDetailRisk {
  risk_level: string;
  trust_level: string;
  approved_count: number;
  rejected_count: number;
  modified_count: number;
  last_decision_at: string | null;
  cooldown_until: string | null;
  graduation_progress: GraduationProgress;
}

export interface TrustCapabilityDetail {
  capability: string;
  family: string;
  ceiling: string;
  risk_levels: TrustCapabilityDetailRisk[];
}

export interface TimePolicyRule {
  start_hour: number;
  end_hour: number;
  max_level: string;
  days?: number[] | null;
}
```

- [ ] **Step 2: Add trust API functions to api.ts**

Add import for the new types at the top of `frontend/src/lib/api.ts`:

```typescript
import type {
  TrustDashboardEntry,
  TrustCapabilityDetail,
  TimePolicyRule,
} from "./types";
```

Append the trust API functions:

```typescript
// ── Trust ──────────────────────────────────────────────────────

export async function fetchTrustDashboard(): Promise<{
  capabilities: TrustDashboardEntry[];
}> {
  return api("/trust/dashboard");
}

export async function fetchTrustCapability(
  capability: string
): Promise<TrustCapabilityDetail> {
  return api(`/trust/${capability}`);
}

export async function setTrustCeiling(
  capability: string,
  maxLevel: string
): Promise<{ capability: string; max_level: string }> {
  return api(`/trust/${capability}/ceiling`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ max_level: maxLevel }),
  });
}

export async function resetTrust(
  capability: string
): Promise<{ capability: string; status: string }> {
  return api(`/trust/${capability}/reset`, { method: "POST" });
}

export async function fetchTimePolicies(): Promise<{
  policies: TimePolicyRule[];
}> {
  return api("/trust-time-policies");
}

export async function setTimePolicies(
  policies: TimePolicyRule[]
): Promise<{ policies: TimePolicyRule[] }> {
  return api("/trust-time-policies", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ policies }),
  });
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(spec2b-ii): frontend trust types + API functions

Add TrustDashboardEntry, TrustCapabilityDetail, TimePolicyRule types.
Add 6 API functions: fetchTrustDashboard, fetchTrustCapability,
setTrustCeiling, resetTrust, fetchTimePolicies, setTimePolicies."
```

---

## Task 6: Frontend Trust Tab in Settings

**Files:**
- Modify: `frontend/src/app/settings/page.tsx`

- [ ] **Step 1: Update settings page with Trust tab**

Replace the entire `frontend/src/app/settings/page.tsx`:

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
import {
  fetchPolicyMode,
  setPolicyMode,
  fetchBudget,
  updateBudgetLimit,
  fetchTrustDashboard,
  setTrustCeiling,
  resetTrust,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth";
import type { TrustDashboardEntry } from "@/lib/types";

type SettingsTab = "account" | "policy" | "trust" | "budget";

const TABS = [
  { key: "account", label: "Account" },
  { key: "policy", label: "Policy" },
  { key: "trust", label: "Trust" },
  { key: "budget", label: "Budget" },
];

const POLICY_MODES = [
  { value: "lockdown", label: "Lockdown", description: "All actions blocked" },
  {
    value: "approval_required",
    label: "Approval Required",
    description: "All actions need approval",
  },
  {
    value: "suggest_only",
    label: "Suggest Only",
    description: "Jarvis suggests, never acts",
  },
  {
    value: "full_auto",
    label: "Full Auto",
    description: "Jarvis acts autonomously",
  },
];

const TRUST_LEVEL_ORDER = ["first_use", "learning", "trusted", "autonomous"];

const TRUST_LEVEL_COLORS: Record<string, string> = {
  first_use: "bg-gray-500",
  learning: "bg-blue-500",
  trusted: "bg-green-500",
  autonomous: "bg-purple-500",
  blocked: "bg-red-500",
};

const TRUST_LEVEL_LABELS: Record<string, string> = {
  first_use: "First Use",
  learning: "Learning",
  trusted: "Trusted",
  autonomous: "Autonomous",
  blocked: "Blocked",
};

const CEILING_OPTIONS = [
  { value: "blocked", label: "Blocked" },
  { value: "first_use", label: "First Use" },
  { value: "learning", label: "Learning" },
  { value: "trusted", label: "Trusted" },
  { value: "autonomous", label: "Autonomous (no limit)" },
];

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("account");
  const [policyMode, setPolicyModeState] = useState("approval_required");
  const [budgetLimit, setBudgetLimit] = useState<number | null>(null);
  const [editingBudget, setEditingBudget] = useState(false);
  const [budgetInput, setBudgetInput] = useState("");
  const [trustEntries, setTrustEntries] = useState<TrustDashboardEntry[]>([]);
  const [trustLoading, setTrustLoading] = useState(false);
  const { user, logout } = useAuth();
  const { addToast } = useToast();

  useEffect(() => {
    fetchPolicyMode()
      .then((r) => setPolicyModeState(r.mode))
      .catch(() => {});
    fetchBudget()
      .then((r) => setBudgetLimit(r.daily_limit_usd))
      .catch(() => {});
  }, []);

  const loadTrust = useCallback(async () => {
    setTrustLoading(true);
    try {
      const data = await fetchTrustDashboard();
      setTrustEntries(data.capabilities);
    } catch {
      addToast("Failed to load trust data", "error");
    } finally {
      setTrustLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    if (activeTab === "trust") {
      loadTrust();
    }
  }, [activeTab, loadTrust]);

  async function handlePolicyChange(mode: string) {
    try {
      await setPolicyMode(mode);
      setPolicyModeState(mode);
      addToast("Policy mode updated", "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    }
  }

  async function handleBudgetSave() {
    const value = parseFloat(budgetInput);
    if (isNaN(value) || value <= 0) return;
    try {
      const res = await updateBudgetLimit(value);
      setBudgetLimit(res.daily_limit_usd);
      setEditingBudget(false);
      addToast("Budget updated", "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    }
  }

  async function handleCeilingChange(capability: string, maxLevel: string) {
    try {
      await setTrustCeiling(capability, maxLevel);
      setTrustEntries((prev) =>
        prev.map((e) =>
          e.capability === capability ? { ...e, ceiling: maxLevel } : e
        )
      );
      addToast(`Ceiling set to ${TRUST_LEVEL_LABELS[maxLevel] ?? maxLevel}`, "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    }
  }

  async function handleResetTrust(capability: string) {
    try {
      await resetTrust(capability);
      await loadTrust();
      addToast(`Trust reset for ${capability}`, "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    }
  }

  // Group trust entries by family
  const trustByFamily: Record<string, TrustDashboardEntry[]> = {};
  for (const entry of trustEntries) {
    const family = entry.family || "unknown";
    if (!trustByFamily[family]) trustByFamily[family] = [];
    trustByFamily[family].push(entry);
  }

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader title="Settings" subtitle="Account, policy, trust, and budget" />
      <Tabs
        tabs={TABS}
        active={activeTab}
        onChange={(k) => setActiveTab(k as SettingsTab)}
      />

      {activeTab === "account" && (
        <Card>
          <CardBody>
            <div className="space-y-4">
              <div>
                <p className="text-xs text-t-muted uppercase mb-1">Email</p>
                <p className="text-sm text-t-primary">
                  {user?.email ?? "—"}
                </p>
              </div>
              <div>
                <p className="text-xs text-t-muted uppercase mb-1">
                  Display Name
                </p>
                <p className="text-sm text-t-primary">
                  {user?.display_name ?? "—"}
                </p>
              </div>
              <button
                onClick={logout}
                className="px-4 py-2 rounded-lg border border-j-error text-j-error text-sm hover:bg-j-error/10 transition-colors"
              >
                Sign Out
              </button>
            </div>
          </CardBody>
        </Card>
      )}

      {activeTab === "policy" && (
        <div className="space-y-3">
          {POLICY_MODES.map((pm) => (
            <Card
              key={pm.value}
              className={
                policyMode === pm.value ? "ring-1 ring-accent-primary" : ""
              }
            >
              <CardBody>
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="radio"
                    name="policy"
                    checked={policyMode === pm.value}
                    onChange={() => handlePolicyChange(pm.value)}
                    className="mt-0.5"
                  />
                  <div>
                    <p className="text-sm font-medium text-t-primary">
                      {pm.label}
                    </p>
                    <p className="text-xs text-t-tertiary">
                      {pm.description}
                    </p>
                  </div>
                </label>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {activeTab === "trust" && (
        <div className="space-y-6">
          {trustLoading && (
            <p className="text-sm text-t-tertiary">Loading trust data...</p>
          )}

          {!trustLoading && trustEntries.length === 0 && (
            <Card>
              <CardBody>
                <p className="text-sm text-t-tertiary">
                  No trust data yet. Trust levels build as Jarvis performs
                  actions and you approve or reject them.
                </p>
              </CardBody>
            </Card>
          )}

          {Object.entries(trustByFamily).map(([family, entries]) => (
            <div key={family}>
              <h3 className="text-xs uppercase text-t-muted mb-2 tracking-wider">
                {family}
              </h3>
              <div className="space-y-2">
                {entries.map((entry) => (
                  <TrustCapabilityCard
                    key={entry.capability}
                    entry={entry}
                    onCeilingChange={handleCeilingChange}
                    onReset={handleResetTrust}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {activeTab === "budget" && (
        <Card>
          <CardBody>
            <div className="space-y-4">
              <div>
                <p className="text-xs text-t-muted uppercase mb-1">
                  Daily Token Budget
                </p>
                {editingBudget ? (
                  <div className="flex items-center gap-2">
                    <span className="text-t-secondary">$</span>
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={budgetInput}
                      onChange={(e) => setBudgetInput(e.target.value)}
                      className="w-32 rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring"
                      autoFocus
                    />
                    <button
                      onClick={handleBudgetSave}
                      className="px-3 py-2 rounded-lg bg-j-primary text-j-primary-fg text-sm hover:bg-j-primary-hover"
                    >
                      Save
                    </button>
                    <button
                      onClick={() => setEditingBudget(false)}
                      className="px-3 py-2 rounded-lg text-t-secondary text-sm hover:bg-surface-2"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-3">
                    <p className="text-lg font-semibold text-t-primary">
                      ${budgetLimit?.toFixed(2) ?? "—"}
                      <span className="text-xs text-t-tertiary font-normal ml-1">
                        / day
                      </span>
                    </p>
                    <button
                      onClick={() => {
                        setBudgetInput(String(budgetLimit ?? 5));
                        setEditingBudget(true);
                      }}
                      className="text-xs text-accent-primary hover:underline"
                    >
                      Edit
                    </button>
                  </div>
                )}
              </div>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

// ── Trust Capability Card ───────────────────────────────────────

interface TrustCapabilityCardProps {
  entry: TrustDashboardEntry;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
}

function TrustCapabilityCard({
  entry,
  onCeilingChange,
  onReset,
}: TrustCapabilityCardProps) {
  const [expanded, setExpanded] = useState(false);

  // Best graduation progress across risk levels
  const bestProgress = entry.risk_levels.reduce(
    (best, rl) => {
      const pct = rl.graduation_progress?.percentage ?? 0;
      return pct > best ? pct : best;
    },
    0
  );

  return (
    <Card>
      <CardBody>
        <div className="space-y-2">
          {/* Header row */}
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center justify-between text-left"
          >
            <div className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-full ${TRUST_LEVEL_COLORS[entry.trust_level] ?? "bg-gray-400"}`}
              />
              <span className="text-sm font-medium text-t-primary">
                {entry.capability}
              </span>
              <span className="text-xs text-t-tertiary">
                {TRUST_LEVEL_LABELS[entry.trust_level] ?? entry.trust_level}
              </span>
            </div>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              className={`text-t-tertiary transition-transform ${expanded ? "rotate-90" : ""}`}
            >
              <path
                d="M9 18l6-6-6-6"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>

          {/* Graduation progress bar */}
          {entry.trust_level !== "autonomous" && (
            <div className="w-full h-1.5 bg-surface-2 rounded-full">
              <div
                className={`h-full rounded-full transition-all ${TRUST_LEVEL_COLORS[entry.trust_level] ?? "bg-gray-400"}`}
                style={{ width: `${Math.min(bestProgress * 100, 100)}%` }}
              />
            </div>
          )}

          {/* Expanded: per-risk breakdown + controls */}
          {expanded && (
            <div className="pt-2 space-y-3 border-t border-b-primary">
              {/* Per-risk-level rows */}
              {entry.risk_levels.map((rl) => (
                <div
                  key={rl.risk_level}
                  className="flex items-center justify-between text-xs"
                >
                  <span className="text-t-secondary w-16">{rl.risk_level}</span>
                  <span
                    className={`px-1.5 py-0.5 rounded ${TRUST_LEVEL_COLORS[rl.trust_level] ?? "bg-gray-400"} text-white`}
                  >
                    {TRUST_LEVEL_LABELS[rl.trust_level] ?? rl.trust_level}
                  </span>
                  <span className="text-t-tertiary">
                    {rl.approved_count}
                    <span className="text-t-muted"> approved</span>
                    {rl.rejected_count > 0 && (
                      <span className="text-red-400 ml-1">
                        {rl.rejected_count} rejected
                      </span>
                    )}
                  </span>
                  {rl.graduation_progress?.next_level && (
                    <span className="text-t-tertiary">
                      {rl.graduation_progress.current}/
                      {rl.graduation_progress.target} to{" "}
                      {TRUST_LEVEL_LABELS[rl.graduation_progress.next_level] ??
                        rl.graduation_progress.next_level}
                    </span>
                  )}
                </div>
              ))}

              {/* Ceiling control */}
              <div className="flex items-center gap-2 pt-2">
                <label className="text-xs text-t-muted">Ceiling:</label>
                <select
                  value={entry.ceiling}
                  onChange={(e) =>
                    onCeilingChange(entry.capability, e.target.value)
                  }
                  className="text-xs rounded bg-surface-2 border border-b-primary px-2 py-1 text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring"
                >
                  {CEILING_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>

                <button
                  onClick={() => onReset(entry.capability)}
                  className="ml-auto text-xs text-j-error hover:underline"
                >
                  Reset Trust
                </button>
              </div>
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/app/settings/page.tsx
git commit -m "feat(spec2b-ii): Trust tab in Settings page

Per-capability trust levels grouped by family, graduation progress bars,
per-risk breakdown on expand, ceiling dropdown control, reset button."
```

---

## Task 7: Activity Store — Auto-Execute Notify Event

**Files:**
- Modify: `frontend/src/stores/activity-store.ts`

- [ ] **Step 1: Add auto_execute_notify to SSE event types**

In `frontend/src/stores/activity-store.ts`, add `"auto_execute_notify"` to the `runtimeTypes` array (line 93-101):

```typescript
    const runtimeTypes = [
      "command_received", "plan_created", "step_routed", "run_created",
      "step_started", "step_completed", "step_failed",
      "approval_requested", "approval_resolved",
      "tool_call_started", "tool_call_completed", "tool_call_failed",
      "artifact_created", "surface_created",
      "agent_started", "agent_completed",
      "run_completed", "run_failed", "run_cancelled",
      "auto_execute_notify",
    ];
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/activity-store.ts
git commit -m "feat(spec2b-ii): add auto_execute_notify to activity store SSE types

Trusted auto-executed actions emit auto_execute_notify events that
appear in the activity feed."
```

---

## Task 8: Check SettingsService for Global Methods + Wire Time Policies

**Files:**
- Modify: `backend/src/services/settings_service.py` (if `get_global`/`set_global` don't exist)
- Modify: `backend/src/services/governor.py` (remove `_get_time_based_policy_override`)

- [ ] **Step 1: Check if SettingsService has get_global/set_global**

Run: `cd backend && grep -n "get_global\|set_global" src/services/settings_service.py`

If these methods don't exist, the TrustEngine time-policy methods from Task 2 need to use a different approach. In that case, update `TrustEngine.get_time_policies()` and `set_time_policies()` to use the existing `SettingsService.get(user_id, ...)` pattern — but since time policies are workspace-scoped (not user-scoped), store them using the workspace_id as a key:

```python
    async def get_time_policies(self) -> list[dict]:
        """Get time-scoped ceiling overrides for this workspace.

        Stored in settings under workspace_id as pseudo-user,
        category="trust", key="time_policies".
        """
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
```

- [ ] **Step 2: Remove _get_time_based_policy_override from Governor**

In `backend/src/services/governor.py`, delete the `_get_time_based_policy_override` method (lines 211-272). Then update `_get_policy_mode` (lines 274-289) to remove the time override call:

```python
    async def _get_policy_mode(self, user_id: str) -> str:
        """Get policy mode from user settings, with fallback."""
        if self._settings_service:
            try:
                mode = await self._settings_service.get_policy_mode(user_id)
                if mode in VALID_POLICY_MODES:
                    return mode
            except Exception:
                logger.warning("Failed to read policy mode for %s", user_id, exc_info=True)
        return "approval_required"
```

Time-based policy overrides are now handled at the TrustEngine layer via time-scoped ceilings, not at the Governor layer via policy mode overrides.

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/governor.py backend/src/services/trust_engine.py
git commit -m "refactor(spec2b-ii): move time-based policy from Governor to TrustEngine

Delete _get_time_based_policy_override from Governor. Time policies now
stored as workspace-scoped trust settings, exposed via trust API."
```

---

## Task 9: Final Verification

- [ ] **Step 1: Run full backend tests**

```bash
cd backend && python -m pytest tests/ -v --timeout=30
```
Expected: All pass.

- [ ] **Step 2: Run ruff check**

```bash
cd backend && ruff check src/ tests/ && ruff format --check src/ tests/
```
Expected: No errors.

- [ ] **Step 3: Run frontend build**

```bash
cd frontend && npm run build
```
Expected: Build succeeds.

- [ ] **Step 4: Grep for dead references**

```bash
cd backend && grep -rn "ApprovalPolicyEngine\|from src.models.trust_score\|from src.models.approval_policy\|from src.services.approval_policy_engine" src/ tests/ --include="*.py" | grep -v __pycache__
```
Expected: Zero results.

- [ ] **Step 5: Verify all 6 endpoints register**

```bash
cd backend && python -c "from src.api.app import create_app; app = create_app(); routes = [r.path for r in app.routes]; assert '/v1/trust/dashboard' in routes; assert '/v1/trust-time-policies' in routes; print('All trust routes registered')"
```

- [ ] **Step 6: Final commit if any fixes needed, then tag**

```bash
git log --oneline -8  # review commit history for this spec
```
