# Proactive Insight Surfaces (Spec 4B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the user-facing proactive insight surface layer — when Jarvis notices something important, it creates a workspace card with suggested actions. Users can act (triggering execution) or dismiss (teaching the system their preferences).

**Architecture:** Spec 4A already routes perception signals into push/briefing/silent tiers. This spec replaces the interim `notifier.notify()` call on push-tier signals with rich insight surfaces. A new `_push_insight_surface()` method creates `WorkspaceSurfacePush` objects with kind `proactive_insight`, pushes via Redis→WS, and persists to `ui_surfaces`. User clicks on a suggested action transition the insight surface into an execution surface (same `surface_id`, phase progression from Spec 3). A new `EngagementHistory` model tracks dismissals/engagements per signal source × category, with suppression rules feeding back into the relevance assessor.

**Tech Stack:** Python 3.12 (async), FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic, Next.js 14, React, Zustand, TypeScript, Redis Pub/Sub, WebSocket

---

## File Map

### New Files (Backend)
| File | Responsibility |
|------|---------------|
| `backend/src/models/engagement_history.py` | SQLAlchemy model for engagement tracking per signal_source × signal_category |
| `backend/src/services/engagement_service.py` | Business logic: record engagement/dismissal, compute suppression, query history |
| `backend/src/api/routes_insights.py` | `POST /v1/insights/{surface_id}/dismiss` + `POST /v1/insights/{surface_id}/act` |
| `backend/alembic/versions/058_add_engagement_history_table.py` | Alembic migration for `engagement_history` table |
| `backend/tests/test_engagement_service.py` | Unit tests for engagement service (suppress/unsuppress/rate calc) |
| `backend/tests/test_insight_surfaces.py` | Unit tests for insight surface push + proposal→execution bridge |

### New Files (Frontend)
| File | Responsibility |
|------|---------------|
| `frontend/src/components/a2ui/components/insight-surface.tsx` | Insight card component (signal source, summary, actions, dismiss) |

### Modified Files (Backend)
| File | Change |
|------|--------|
| `backend/src/orchestrator/contracts.py:231` | Add `"proactive_insight"` to `WorkspaceSurfacePush.kind` Literal |
| `backend/src/services/relevance_assessor.py:100` | Accept optional `engagement_context` parameter in `assess_relevance()` |
| `backend/src/orchestrator/jarvis.py:1480` | Replace interim notifier call with `_push_insight_surface()` method |
| `backend/src/services/surface_builder.py:49` | Add `_build_insight_surfaces()` for workspace reconnection |
| `backend/src/api/app.py:269` | Register `routes_insights` router |
| `backend/src/api/routes_ws.py:280` | Add `"execute_insight"` action handler |
| `backend/src/models/__init__.py:1` | Add `EngagementHistory` import + `__all__` entry |

### Modified Files (Frontend)
| File | Change |
|------|--------|
| `frontend/src/lib/types/surfaces.ts:1` | Add `"proactive_insight"` to `SurfaceKind` union |
| `frontend/src/lib/a2ui-types.ts:42` | Add `"proposal"` to `SurfacePreview.status` union, add `InsightData` interface |
| `frontend/src/lib/api.ts` | Add `dismissInsight()` API function |
| `frontend/src/components/workspace/surface-card.tsx:11` | Add `proactive_insight` border color + `proposal` status dot |
| `frontend/src/stores/surface-store.ts:33` | Add `transitionToExecution()` action for insight→execution lifecycle |
| `frontend/src/app/page.tsx:66` | Include `proposal` phase in active surface sort |

---

## Task 1: EngagementHistory Model + Migration

**Files:**
- Create: `backend/src/models/engagement_history.py`
- Modify: `backend/src/models/__init__.py`
- Create: `backend/alembic/versions/058_add_engagement_history_table.py`

- [ ] **Step 1: Write the EngagementHistory model**

```python
# backend/src/models/engagement_history.py
"""Engagement history for proactive insight surfaces.

Tracks per signal_source × signal_category how often the user engages,
dismisses, or ignores insight surfaces. Drives suppression rules.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class EngagementHistory(Base, TimestampMixin):
    __tablename__ = "engagement_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    signal_source: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_category: Mapped[str] = mapped_column(String(64), nullable=False)
    engaged_count: Mapped[int] = mapped_column(Integer, default=0)
    dismissed_count: Mapped[int] = mapped_column(Integer, default=0)
    ignored_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_dismissals: Mapped[int] = mapped_column(Integer, default=0)
    engagement_rate: Mapped[float] = mapped_column(Float, default=0.5)
    last_engaged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "signal_source", "signal_category",
            name="uq_engagement_ws_source_cat",
        ),
        Index("ix_engagement_workspace", "workspace_id"),
    )
```

- [ ] **Step 2: Add import to models/__init__.py**

Add to `backend/src/models/__init__.py`:

```python
# After the existing import for InteractionLog:
from src.models.engagement_history import EngagementHistory
```

And add `"EngagementHistory"` to the `__all__` list after `"InteractionLog"`.

- [ ] **Step 3: Create the Alembic migration**

Run: `cd backend && alembic revision --autogenerate -m "add engagement_history table"`

Verify the generated migration creates the `engagement_history` table with the unique constraint and index. The migration should look like:

```python
"""add engagement_history table

Revision ID: <auto>
Revises: <head>
Create Date: <auto>
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "<auto>"
down_revision = "<latest>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engagement_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("signal_source", sa.String(64), nullable=False),
        sa.Column("signal_category", sa.String(64), nullable=False),
        sa.Column("engaged_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dismissed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ignored_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consecutive_dismissals", sa.Integer(), server_default="0", nullable=False),
        sa.Column("engagement_rate", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("last_engaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppressed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "signal_source",
            "signal_category",
            name="uq_engagement_ws_source_cat",
        ),
    )
    op.create_index("ix_engagement_workspace", "engagement_history", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_engagement_workspace", table_name="engagement_history")
    op.drop_table("engagement_history")
```

- [ ] **Step 4: Run migration**

Run: `cd backend && alembic upgrade head`
Expected: Migration applies successfully.

- [ ] **Step 5: Commit**

```bash
git add backend/src/models/engagement_history.py backend/src/models/__init__.py backend/alembic/versions/058_*
git commit -m "feat(spec4b): add EngagementHistory model + migration"
```

---

## Task 2: Engagement Service

**Files:**
- Create: `backend/src/services/engagement_service.py`
- Test: `backend/tests/test_engagement_service.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_engagement_service.py
"""Tests for EngagementService — suppression rules, rate calculation."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.engagement_service import EngagementService


def _make_mock_db():
    """Create a mock AsyncSession with execute/commit/add."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _make_history_row(
    engaged=0,
    dismissed=0,
    ignored=0,
    consecutive_dismissals=0,
    suppressed=False,
):
    """Create a mock EngagementHistory row."""
    row = MagicMock()
    row.engaged_count = engaged
    row.dismissed_count = dismissed
    row.ignored_count = ignored
    row.consecutive_dismissals = consecutive_dismissals
    row.engagement_rate = (
        engaged / max(engaged + dismissed + ignored, 1)
    )
    row.suppressed = suppressed
    row.last_engaged_at = None
    row.last_dismissed_at = None
    return row


@pytest.mark.asyncio
async def test_record_engagement_resets_consecutive_dismissals():
    db = _make_mock_db()
    row = _make_history_row(engaged=2, dismissed=3, consecutive_dismissals=3)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("gmail", "reply", "engaged")

    assert row.consecutive_dismissals == 0
    assert row.engaged_count == 3
    assert row.suppressed is False


@pytest.mark.asyncio
async def test_record_dismissal_increments_consecutive():
    db = _make_mock_db()
    row = _make_history_row(dismissed=2, consecutive_dismissals=2)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("gmail", "reply", "dismissed")

    assert row.consecutive_dismissals == 3
    assert row.dismissed_count == 3


@pytest.mark.asyncio
async def test_suppression_at_5_consecutive_dismissals():
    db = _make_mock_db()
    row = _make_history_row(dismissed=4, consecutive_dismissals=4)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("gmail", "reply", "dismissed")

    assert row.consecutive_dismissals == 5
    assert row.suppressed is True


@pytest.mark.asyncio
async def test_engagement_on_suppressed_removes_suppression():
    db = _make_mock_db()
    row = _make_history_row(
        engaged=0, dismissed=5, consecutive_dismissals=5, suppressed=True
    )
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("gmail", "reply", "engaged")

    assert row.suppressed is False
    assert row.consecutive_dismissals == 0


@pytest.mark.asyncio
async def test_relevance_penalty_at_3_consecutive_dismissals():
    db = _make_mock_db()
    row = _make_history_row(dismissed=3, consecutive_dismissals=3)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    penalty = await svc.get_relevance_penalty("gmail", "reply")
    assert penalty == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_no_penalty_below_3_dismissals():
    db = _make_mock_db()
    row = _make_history_row(dismissed=1, consecutive_dismissals=1)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    penalty = await svc.get_relevance_penalty("gmail", "reply")
    assert penalty == 0.0


@pytest.mark.asyncio
async def test_suppressed_source_returns_full_penalty():
    db = _make_mock_db()
    row = _make_history_row(
        dismissed=5, consecutive_dismissals=5, suppressed=True
    )
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    penalty = await svc.get_relevance_penalty("gmail", "reply")
    assert penalty == 1.0


@pytest.mark.asyncio
async def test_creates_new_row_on_first_engagement():
    db = _make_mock_db()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    svc = EngagementService(db, "ws_test")
    await svc.record_engagement("github", "pr_review", "engaged")

    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_get_engagement_context_returns_formatted_context():
    db = _make_mock_db()
    svc = EngagementService(db, "ws_test")

    row1 = _make_history_row(dismissed=3, consecutive_dismissals=3)
    row1.signal_source = "gmail"
    row1.signal_category = "reply"
    row1.suppressed = False
    row1.engagement_rate = 0.2

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [row1]
    db.execute.return_value = result_mock

    context = await svc.get_engagement_context()
    assert "gmail" in context
    assert "reply" in context
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_engagement_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.engagement_service'`

- [ ] **Step 3: Write the EngagementService**

```python
# backend/src/services/engagement_service.py
"""Engagement tracking for proactive insight surfaces.

Tracks how users respond to insight surfaces per signal_source × signal_category.
Drives suppression rules:
- 3+ consecutive dismissals → relevance penalty of 0.2
- 5+ consecutive dismissals → auto-suppress (penalty 1.0)
- Any engagement on suppressed type → remove suppression
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.engagement_history import EngagementHistory

logger = logging.getLogger(__name__)

# Suppression thresholds
_PENALTY_THRESHOLD = 3
_SUPPRESS_THRESHOLD = 5
_RELEVANCE_PENALTY = 0.2


class EngagementService:
    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def _get_or_create(
        self, signal_source: str, signal_category: str
    ) -> EngagementHistory:
        result = await self._db.execute(
            select(EngagementHistory).where(
                EngagementHistory.workspace_id == self._workspace_id,
                EngagementHistory.signal_source == signal_source,
                EngagementHistory.signal_category == signal_category,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            return row

        row = EngagementHistory(
            workspace_id=self._workspace_id,
            signal_source=signal_source,
            signal_category=signal_category,
        )
        self._db.add(row)
        return row

    async def record_engagement(
        self,
        signal_source: str,
        signal_category: str,
        action: str,
    ) -> None:
        """Record an engagement, dismissal, or ignore event.

        Args:
            signal_source: e.g. "gmail", "github", "calendar"
            signal_category: e.g. "reply", "pr_review", "meeting_update"
            action: "engaged", "dismissed", or "ignored"
        """
        row = await self._get_or_create(signal_source, signal_category)
        now = datetime.now(timezone.utc)

        if action == "engaged":
            row.engaged_count += 1
            row.consecutive_dismissals = 0
            row.last_engaged_at = now
            if row.suppressed:
                row.suppressed = False
        elif action == "dismissed":
            row.dismissed_count += 1
            row.consecutive_dismissals += 1
            row.last_dismissed_at = now
            if row.consecutive_dismissals >= _SUPPRESS_THRESHOLD:
                row.suppressed = True
        elif action == "ignored":
            row.ignored_count += 1

        total = row.engaged_count + row.dismissed_count + row.ignored_count
        row.engagement_rate = row.engaged_count / max(total, 1)

    async def get_relevance_penalty(
        self, signal_source: str, signal_category: str
    ) -> float:
        """Return relevance penalty for a signal source × category.

        Returns:
            0.0 — no penalty
            0.2 — 3+ consecutive dismissals
            1.0 — suppressed (5+ consecutive dismissals)
        """
        result = await self._db.execute(
            select(EngagementHistory).where(
                EngagementHistory.workspace_id == self._workspace_id,
                EngagementHistory.signal_source == signal_source,
                EngagementHistory.signal_category == signal_category,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            return 0.0
        if row.suppressed:
            return 1.0
        if row.consecutive_dismissals >= _PENALTY_THRESHOLD:
            return _RELEVANCE_PENALTY
        return 0.0

    async def is_suppressed(
        self, signal_source: str, signal_category: str
    ) -> bool:
        """Check if a signal source × category is suppressed."""
        result = await self._db.execute(
            select(EngagementHistory.suppressed).where(
                EngagementHistory.workspace_id == self._workspace_id,
                EngagementHistory.signal_source == signal_source,
                EngagementHistory.signal_category == signal_category,
            )
        )
        val = result.scalar_one_or_none()
        return bool(val)

    async def get_engagement_context(self) -> str:
        """Build a text summary of engagement patterns for the relevance assessor.

        Returns a formatted string describing signal types with low engagement
        or suppression, so the LLM can factor user preferences into scoring.
        """
        result = await self._db.execute(
            select(EngagementHistory).where(
                EngagementHistory.workspace_id == self._workspace_id,
                EngagementHistory.consecutive_dismissals >= _PENALTY_THRESHOLD,
            )
        )
        rows = result.scalars().all()
        if not rows:
            return ""

        lines = ["User engagement patterns (low engagement signals):"]
        for r in rows:
            status = "SUPPRESSED" if r.suppressed else "low engagement"
            lines.append(
                f"- {r.signal_source}/{r.signal_category}: "
                f"{status}, engagement rate {r.engagement_rate:.0%}, "
                f"{r.consecutive_dismissals} consecutive dismissals"
            )
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_engagement_service.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/engagement_service.py backend/tests/test_engagement_service.py
git commit -m "feat(spec4b): add EngagementService with suppression rules"
```

---

## Task 3: Backend Contracts — Add `proactive_insight` Surface Kind

**Files:**
- Modify: `backend/src/orchestrator/contracts.py:231`
- Modify: `backend/src/services/relevance_assessor.py:100`

- [ ] **Step 1: Add `proactive_insight` to `WorkspaceSurfacePush.kind` Literal**

In `backend/src/orchestrator/contracts.py`, find the `kind` field on `WorkspaceSurfacePush` (line 231) and add `"proactive_insight"` to the Literal union:

```python
    kind: Literal[
        "summary",
        "briefing",
        "plan",
        "checklist",
        "approval",
        "comparison",
        "alert",
        "timeline",
        "table",
        "recommendation",
        "activity",
        "proactive_insight",
    ]
```

- [ ] **Step 2: Add `InsightSurfaceData` model to contracts.py**

Add after the `WorkspaceSurfacePush` class (around line 252):

```python
class SuggestedActionRef(BaseModel):
    """Reference to a suggested action stored in the surface payload."""

    model_config = ConfigDict(extra="ignore")

    description: str
    capability: str
    action_input: dict[str, Any] = Field(default_factory=dict)


class InsightSurfaceData(BaseModel):
    """Data payload for proactive_insight surfaces, stored in UISurface.payload."""

    model_config = ConfigDict(extra="ignore")

    signal_source: str
    signal_category: str = ""
    signal_summary: str
    relevance_score: float = 0.0
    relevance_reasoning: str = ""
    related_goals: list[str] = Field(default_factory=list)
    suggested_actions: list[SuggestedActionRef] = Field(default_factory=list)
    dismiss_available: bool = True
```

- [ ] **Step 3: Add `engagement_context` parameter to `assess_relevance()`**

In `backend/src/services/relevance_assessor.py`, modify `assess_relevance()` to accept an optional engagement context string and append it to the prompt:

Find the function signature at line 100:
```python
async def assess_relevance(
    signal: PerceptionSignal,
    user_context: UserContext,
    client: Any,
    model: str = "claude-haiku-4-5-20251001",
) -> RelevanceAssessment:
```

Replace with:
```python
async def assess_relevance(
    signal: PerceptionSignal,
    user_context: UserContext,
    client: Any,
    model: str = "claude-haiku-4-5-20251001",
    engagement_context: str = "",
) -> RelevanceAssessment:
```

And in the prompt construction (line 108), after `summary=signal.summary`:

Find:
```python
        prompt = _RELEVANCE_PROMPT.format(
            goals=", ".join(user_context.goals) or "none specified",
            recent_activity=user_context.recent_activity or "none",
            preferences=", ".join(user_context.preferences) or "none",
            source=signal.source,
            event_type=signal.event_type,
            summary=signal.summary,
        )
```

Replace with:
```python
        prompt = _RELEVANCE_PROMPT.format(
            goals=", ".join(user_context.goals) or "none specified",
            recent_activity=user_context.recent_activity or "none",
            preferences=", ".join(user_context.preferences) or "none",
            source=signal.source,
            event_type=signal.event_type,
            summary=signal.summary,
        )
        if engagement_context:
            prompt += f"\n\nEngagement history:\n{engagement_context}"
```

- [ ] **Step 4: Run existing tests**

Run: `cd backend && python -m pytest tests/ -v -k "relevance" --no-header -q`
Expected: Existing relevance assessor tests still pass (new parameter has a default).

- [ ] **Step 5: Commit**

```bash
git add backend/src/orchestrator/contracts.py backend/src/services/relevance_assessor.py
git commit -m "feat(spec4b): add proactive_insight surface kind + InsightSurfaceData contract"
```

---

## Task 4: Insight Surface Push + Wire into Perception

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:1480`
- Test: `backend/tests/test_insight_surfaces.py`

- [ ] **Step 1: Write failing tests for insight surface push**

```python
# backend/tests/test_insight_surfaces.py
"""Tests for proactive insight surface creation and push."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_orchestrator():
    """Create a minimal mock orchestrator with the fields _push_insight_surface needs."""
    orch = MagicMock()
    orch._db_factory = MagicMock()
    orch._event_bus = AsyncMock()

    mock_event_bus = AsyncMock()
    mock_event_bus.publish_to_channel = AsyncMock()
    orch._ensure_event_bus = AsyncMock(return_value=mock_event_bus)

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    orch._db_factory.return_value = mock_db

    return orch, mock_event_bus, mock_db


def test_insight_surface_data_model():
    """InsightSurfaceData validates correctly."""
    from src.orchestrator.contracts import InsightSurfaceData

    data = InsightSurfaceData(
        signal_source="gmail",
        signal_category="reply",
        signal_summary="Sarah replied about Series A",
        relevance_score=0.85,
        relevance_reasoning="Relates to active fundraising goal",
        related_goals=["Close Series A"],
        suggested_actions=[],
    )
    assert data.signal_source == "gmail"
    assert data.dismiss_available is True


def test_insight_surface_data_with_actions():
    """InsightSurfaceData with suggested actions."""
    from src.orchestrator.contracts import InsightSurfaceData, SuggestedActionRef

    action = SuggestedActionRef(
        description="Draft reply to Sarah",
        capability="email.draft",
        action_input={"to": "sarah@example.com"},
    )
    data = InsightSurfaceData(
        signal_source="gmail",
        signal_summary="Sarah replied",
        suggested_actions=[action],
    )
    assert len(data.suggested_actions) == 1
    assert data.suggested_actions[0].capability == "email.draft"


def test_workspace_surface_push_accepts_proactive_insight():
    """WorkspaceSurfacePush accepts kind='proactive_insight'."""
    from src.orchestrator.contracts import WorkspaceSurfacePush

    surface = WorkspaceSurfacePush(
        id="surf_test123",
        kind="proactive_insight",
        preview={"title": "Test", "subtitle": None, "status": "proposal",
                 "priority": "high", "metrics": [], "entities": [],
                 "progress": None, "timestamp": None, "tags": ["gmail"]},
        detail_config=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert surface.kind == "proactive_insight"
```

- [ ] **Step 2: Run tests to verify they pass (contract tests) and fail (push tests)**

Run: `cd backend && python -m pytest tests/test_insight_surfaces.py -v`
Expected: The contract model tests PASS (contracts already updated in Task 3).

- [ ] **Step 3: Add `_push_insight_surface()` method to jarvis.py**

In `backend/src/orchestrator/jarvis.py`, add this method after `_push_workspace_surface` (around line 1990):

```python
    async def _push_insight_surface(
        self,
        signal: "PerceptionSignal",
        assessment: "RelevanceAssessment",
        user_id: str,
        workspace_id: str,
    ) -> None:
        """Push a proactive insight surface to the workspace.

        Called when the relevance assessor routes a signal to the push tier.
        Creates a WorkspaceSurfacePush with kind='proactive_insight' and
        persists to ui_surfaces for workspace reconnection.
        """
        from datetime import datetime, timedelta, timezone

        from ulid import ULID

        from src.orchestrator.contracts import (
            InsightSurfaceData,
            SuggestedActionRef,
            WorkspaceSurfacePush,
        )
        from src.ui.contracts import SurfacePreview

        try:
            event_bus = await self._ensure_event_bus()
            if not event_bus:
                return

            surface_id = f"surf_{ULID()}"

            suggested_actions = [
                SuggestedActionRef(
                    description=a.description,
                    capability=a.capability,
                    action_input=a.action_input,
                )
                for a in assessment.suggested_actions
            ]

            insight_data = InsightSurfaceData(
                signal_source=signal.source,
                signal_category=signal.event_type,
                signal_summary=signal.summary,
                relevance_score=assessment.relevance_score,
                relevance_reasoning=assessment.reasoning,
                related_goals=assessment.relates_to_goals,
                suggested_actions=suggested_actions,
            )

            preview = SurfacePreview(
                title=signal.summary[:120],
                subtitle=assessment.reasoning[:200] if assessment.reasoning else None,
                status="proposal",
                priority="high" if assessment.urgency == "immediate" else "medium",
                tags=[signal.source],
            )

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind="proactive_insight",
                preview=preview.model_dump(mode="json"),
                detail_config=None,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            # Include insight data in the payload for the frontend
            surface_payload = surface.model_dump(mode="json")
            surface_payload["insight_data"] = insight_data.model_dump(mode="json")

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps({"type": "surface", "surface": surface_payload})
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to ui_surfaces
            try:
                from src.models.ui_state import UISurface

                async with self._db_factory() as db:
                    db.add(
                        UISurface(
                            surface_id=surface_id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type="proactive_insight",
                            payload=surface_payload,
                            preview=preview.model_dump(mode="json"),
                            detail_config=None,
                            expires_at=datetime.now(timezone.utc)
                            + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug(
                    "Failed to persist insight surface to DB", exc_info=True
                )

        except Exception:
            logger.warning("Failed to push insight surface", exc_info=True)
```

- [ ] **Step 4: Replace interim notifier call with `_push_insight_surface()`**

In `backend/src/orchestrator/jarvis.py`, find the push tier handling at line 1480:

```python
                elif assessment.notification_tier == "push":
                    # Notify via existing notifier (interim until Spec 4B surfaces)
                    try:
                        notifier = self._services.notifier if self._services else None
                        if notifier:
                            await notifier.notify(
                                user_id=user_id,
                                notification_type="insight",
                                title=f"Signal from {source}",
                                body=assessment.reasoning[:200],
                                data={
                                    "urgency": 0.8 if assessment.urgency == "immediate" else 0.6,
                                    "goal_relevance": assessment.relevance_score,
                                    "novelty": 0.7,
                                    "signal_source": source,
                                },
                                workspace_id=workspace_id,
                            )
                    except Exception:
                        logger.warning("Failed to push notification for signal", exc_info=True)
```

Replace with:

```python
                elif assessment.notification_tier == "push":
                    try:
                        await self._push_insight_surface(
                            signal, assessment, user_id, workspace_id
                        )
                    except Exception:
                        logger.warning(
                            "Failed to push insight surface for signal",
                            exc_info=True,
                        )
```

- [ ] **Step 5: Wire engagement context into relevance assessment call**

In the same perception cycle method, find the `assess_relevance` call (around line 1461):

```python
                assessment = await assess_relevance(signal, user_context, self._client)
```

Replace with:

```python
                # Fetch engagement context for the assessor
                engagement_context = ""
                try:
                    from src.services.engagement_service import EngagementService

                    async with self._db_factory() as db:
                        eng_svc = EngagementService(db, workspace_id)
                        if await eng_svc.is_suppressed(
                            signal.source, signal.event_type
                        ):
                            logger.debug(
                                "Signal suppressed: %s/%s",
                                signal.source,
                                signal.event_type,
                            )
                            continue
                        engagement_context = await eng_svc.get_engagement_context()
                except Exception:
                    logger.debug("Failed to load engagement context", exc_info=True)

                assessment = await assess_relevance(
                    signal,
                    user_context,
                    self._client,
                    engagement_context=engagement_context,
                )
```

Note: This adds a `continue` that skips suppressed signals before they even reach the LLM assessor — saving API calls for signals the user has explicitly said they don't want.

- [ ] **Step 6: Run tests**

Run: `cd backend && python -m pytest tests/test_insight_surfaces.py tests/test_engagement_service.py -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/orchestrator/jarvis.py backend/tests/test_insight_surfaces.py
git commit -m "feat(spec4b): add _push_insight_surface + wire into push-tier perception"
```

---

## Task 5: Surface Builder — Include Insight Surfaces on Reconnect

**Files:**
- Modify: `backend/src/services/surface_builder.py:49`

- [ ] **Step 1: Add `_build_insight_surfaces()` method to `SurfaceService`**

In `backend/src/services/surface_builder.py`, add a new method and wire it into `build_workspace_surfaces()`.

Add the method after `_build_recommendation_surfaces` (around line 381):

```python
    async def _build_insight_surfaces(self, user_id: str) -> list[dict[str, Any]]:
        """Load persisted proactive insight surfaces that haven't expired."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            select(UISurface)
            .where(
                UISurface.user_id == user_id,
                UISurface.workspace_id == self._workspace_id,
                UISurface.surface_type == "proactive_insight",
                UISurface.expires_at > now,
            )
            .order_by(UISurface.updated_at.desc())
            .limit(10)
        )
        rows = result.scalars().all()
        surfaces: list[dict[str, Any]] = []

        for db_row in rows:
            try:
                payload = db_row.payload or {}
                preview_data = db_row.preview or payload.get("preview")
                if not preview_data:
                    continue

                surfaces.append(
                    {
                        "id": payload.get("id", db_row.surface_id),
                        "kind": "proactive_insight",
                        "preview": preview_data,
                        "detail_config": None,
                        "insight_data": payload.get("insight_data"),
                        "created_at": (
                            db_row.created_at.isoformat()
                            if db_row.created_at
                            else None
                        ),
                    }
                )
            except Exception:
                logger.debug(
                    "Failed to parse insight surface %s",
                    db_row.surface_id,
                    exc_info=True,
                )

        return surfaces
```

- [ ] **Step 2: Wire into `build_workspace_surfaces()`**

Find in `build_workspace_surfaces()`:

```python
        surfaces.extend(await self._build_recommendation_surfaces())
        surfaces.extend(await self._load_persisted_surfaces(user_id))
```

Replace with:

```python
        surfaces.extend(await self._build_insight_surfaces(user_id))
        surfaces.extend(await self._build_recommendation_surfaces())
        surfaces.extend(await self._load_persisted_surfaces(user_id))
```

This places insight surfaces above recommendations in priority (after approvals, active executions, priority alerts, and briefings).

- [ ] **Step 3: Exclude insight surfaces from `_load_persisted_surfaces` to avoid duplicates**

In `_load_persisted_surfaces`, add a filter to exclude `proactive_insight` surfaces (they're already loaded by the dedicated method):

Find:
```python
                UISurface.expires_at > now,
            )
            .order_by(UISurface.updated_at.desc())
```

Replace with:
```python
                UISurface.expires_at > now,
                UISurface.surface_type != "proactive_insight",
            )
            .order_by(UISurface.updated_at.desc())
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/ -v -k "surface" --no-header -q`
Expected: All surface-related tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/surface_builder.py
git commit -m "feat(spec4b): include insight surfaces in workspace rebuild on reconnect"
```

---

## Task 6: Dismiss API + Execute Insight Action Handler

**Files:**
- Create: `backend/src/api/routes_insights.py`
- Modify: `backend/src/api/app.py`
- Modify: `backend/src/api/routes_ws.py:280`

- [ ] **Step 1: Create `routes_insights.py` with dismiss endpoint**

```python
# backend/src/api/routes_insights.py
"""Insight surface endpoints — dismiss and execute suggested actions."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.ui_state import UISurface

logger = logging.getLogger(__name__)

router = APIRouter()


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str | None = None


class DismissResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str = "dismissed"
    surface_id: str


@router.post(
    "/v1/insights/{surface_id}/dismiss",
    response_model=DismissResponse,
)
async def dismiss_insight(
    surface_id: str,
    body: DismissRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Dismiss a proactive insight surface and record in engagement history."""
    result = await db.execute(
        select(UISurface).where(
            UISurface.surface_id == surface_id,
            UISurface.user_id == user_id,
            UISurface.workspace_id == workspace_id,
            UISurface.surface_type == "proactive_insight",
        )
    )
    surface = result.scalar_one_or_none()
    if not surface:
        raise HTTPException(status_code=404, detail="Insight surface not found")

    # Record dismissal in engagement history
    payload = surface.payload or {}
    insight_data = payload.get("insight_data", {})
    signal_source = insight_data.get("signal_source", "unknown")
    signal_category = insight_data.get("signal_category", "unknown")

    from src.services.engagement_service import EngagementService

    eng_svc = EngagementService(db, workspace_id)
    await eng_svc.record_engagement(signal_source, signal_category, "dismissed")

    # Remove surface
    await db.delete(surface)
    await db.commit()

    return DismissResponse(surface_id=surface_id)
```

- [ ] **Step 2: Register router in app.py**

In `backend/src/api/app.py`, add the import (after the existing router imports, around line 38):

```python
from src.api.routes_insights import router as insights_router
```

And add the include (after the last `app.include_router` call):

```python
    app.include_router(insights_router, tags=["insights"])
```

- [ ] **Step 3: Add `execute_insight` action handler to routes_ws.py**

In `backend/src/api/routes_ws.py`, add a new handler before the `ACTION_HANDLERS` dict:

```python
async def _handle_execute_insight(user_id: str, payload: dict, app) -> dict:
    """Handle insight action execution — transitions insight to execution surface.

    When a user clicks a suggested action on an insight surface, this handler:
    1. Fetches the insight surface and the selected action
    2. Creates a PlanOutput from the suggested action
    3. Executes via the orchestrator (which creates a TaskRun)
    4. The surface transitions from proposal to execution (same surface_id)
    """
    from src.api.deps import resolve_workspace_id
    from src.config.settings import get_settings
    from src.models.database import get_session_factory

    surface_id = payload.get("surface_id", "")
    action_index = payload.get("action_index", 0)

    if not surface_id:
        return {"status": "error", "error": "surface_id required"}

    settings = get_settings()

    async with get_session_factory()() as db:
        try:
            workspace_id = await resolve_workspace_id(db, user_id)
        except Exception as e:
            logger.warning("ws_insight_workspace_resolve_failed: %s", e)
            return {"status": "error", "error": "Could not resolve workspace"}

        # Fetch the insight surface
        from sqlalchemy import select
        from src.models.ui_state import UISurface

        result = await db.execute(
            select(UISurface).where(
                UISurface.surface_id == surface_id,
                UISurface.user_id == user_id,
                UISurface.surface_type == "proactive_insight",
            )
        )
        surface = result.scalar_one_or_none()
        if not surface:
            return {"status": "error", "error": "Insight surface not found"}

        payload_data = surface.payload or {}
        insight_data = payload_data.get("insight_data", {})
        actions = insight_data.get("suggested_actions", [])

        if action_index >= len(actions):
            return {"status": "error", "error": "Invalid action index"}

        selected = actions[action_index]

        # Record engagement
        from src.services.engagement_service import EngagementService

        eng_svc = EngagementService(db, workspace_id)
        await eng_svc.record_engagement(
            insight_data.get("signal_source", "unknown"),
            insight_data.get("signal_category", "unknown"),
            "engaged",
        )
        await db.commit()

    # Create PlanOutput and execute via orchestrator
    orchestrator = getattr(app.state, "orchestrator", None)
    if not orchestrator:
        return {"status": "error", "error": "Orchestrator not available"}

    from src.orchestrator.contracts import PlanOutput, PlanStep

    plan = PlanOutput(
        goal=selected.get("description", "Execute suggested action"),
        steps=[
            PlanStep(
                step_id="step_1",
                description=selected.get("description", ""),
                capability=selected.get("capability", ""),
                input=selected.get("action_input", {}),
            )
        ],
        achievable="full",
    )

    try:
        result = await orchestrator.process_message(
            user_id=user_id,
            workspace_id=workspace_id,
            message=f"[Execute insight action] {plan.goal}",
            surface_id=surface_id,
        )
        return {
            "status": "success",
            "surface_id": surface_id,
            "action": selected.get("description", ""),
        }
    except Exception as e:
        logger.warning("ws_execute_insight_failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}
```

Then add to the `ACTION_HANDLERS` dict:

```python
ACTION_HANDLERS: dict[str, object] = {
    "approve": _handle_approve,
    "reject": _handle_reject,
    "execute_insight": _handle_execute_insight,
}
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/ -v -k "insight or engagement" --no-header -q`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes_insights.py backend/src/api/app.py backend/src/api/routes_ws.py
git commit -m "feat(spec4b): add dismiss API + execute_insight WS action handler"
```

---

## Task 7: Frontend Types — Surface Kind + Insight Data

**Files:**
- Modify: `frontend/src/lib/types/surfaces.ts`
- Modify: `frontend/src/lib/a2ui-types.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add `proactive_insight` to SurfaceKind**

In `frontend/src/lib/types/surfaces.ts`, add `"proactive_insight"` to the union:

```typescript
export type SurfaceKind =
  | "summary"
  | "briefing"
  | "plan"
  | "checklist"
  | "approval"
  | "comparison"
  | "alert"
  | "timeline"
  | "table"
  | "recommendation"
  | "activity"
  | "execution"
  | "proactive_insight";
```

- [ ] **Step 2: Add `proposal` to SurfacePreview.status and InsightData types**

In `frontend/src/lib/a2ui-types.ts`, update `SurfacePreview.status` (line 34):

```typescript
export interface SurfacePreview {
  title: string;
  subtitle: string | null;
  status:
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "awaiting_approval"
    | "cancelled"
    | "proposal"
    | null;
  priority: "low" | "medium" | "high" | "critical" | null;
  metrics: SurfaceMetric[];
  entities: string[];
  progress: number | null;
  timestamp: string | null;
  tags: string[];
}
```

Add `InsightData` and `SuggestedActionRef` interfaces after `WorkspaceSurfacePush` (line 87):

```typescript
// ── Insight surface types ────────────────────────────────────

export interface SuggestedActionRef {
  description: string;
  capability: string;
  action_input: Record<string, unknown>;
}

export interface InsightData {
  signal_source: string;
  signal_category: string;
  signal_summary: string;
  relevance_score: number;
  relevance_reasoning: string;
  related_goals: string[];
  suggested_actions: SuggestedActionRef[];
  dismiss_available: boolean;
}
```

- [ ] **Step 3: Add `dismissInsight()` API function**

In `frontend/src/lib/api.ts`, add after the `fetchSurfaceDetail` function:

```typescript
// ── Insights ───────────────────────────────────────────────────

export function dismissInsight(
  surfaceId: string,
  reason?: string
): Promise<{ status: string; surface_id: string }> {
  return post(`/insights/${surfaceId}/dismiss`, { reason: reason || null });
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types/surfaces.ts frontend/src/lib/a2ui-types.ts frontend/src/lib/api.ts
git commit -m "feat(spec4b): add proactive_insight frontend types + dismiss API"
```

---

## Task 8: Insight Surface Component

**Files:**
- Create: `frontend/src/components/a2ui/components/insight-surface.tsx`

- [ ] **Step 1: Create the insight surface component**

```tsx
// frontend/src/components/a2ui/components/insight-surface.tsx
"use client";

import type { InsightData } from "@/lib/a2ui-types";
import { dismissInsight } from "@/lib/api";
import { useSurfaceStore } from "@/stores/surface-store";
import { useWsActionStore } from "@/stores/ws-action-store";
import { useState } from "react";

const sourceIcons: Record<string, string> = {
  gmail: "\u2709\uFE0F",
  github: "\uD83D\uDC19",
  calendar: "\uD83D\uDCC5",
  slack: "\uD83D\uDCAC",
  linear: "\uD83D\uDCCB",
};

interface InsightSurfaceProps {
  surfaceId: string;
  insightData: InsightData;
}

export function InsightSurface({ surfaceId, insightData }: InsightSurfaceProps) {
  const [dismissing, setDismissing] = useState(false);
  const [acting, setActing] = useState<number | null>(null);
  const removeSurface = useSurfaceStore((s) => s.removeSurface);
  const sendAction = useWsActionStore((s) => s.sendAction);

  const handleDismiss = async () => {
    setDismissing(true);
    try {
      await dismissInsight(surfaceId);
      removeSurface(surfaceId);
    } catch {
      setDismissing(false);
    }
  };

  const handleAction = (index: number) => {
    if (!sendAction) return;
    setActing(index);
    sendAction("execute_insight", {
      surface_id: surfaceId,
      action_index: index,
    });
  };

  const icon = sourceIcons[insightData.signal_source] ?? "\uD83D\uDD14";

  return (
    <div className="space-y-3">
      {/* Source badge */}
      <div className="flex items-center gap-2">
        <span className="text-base">{icon}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-400 font-medium uppercase tracking-wide">
          {insightData.signal_source}
        </span>
        {insightData.relevance_score >= 0.8 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-medium">
            High relevance
          </span>
        )}
      </div>

      {/* Signal summary */}
      <p className="text-sm text-t-primary font-medium">
        {insightData.signal_summary}
      </p>

      {/* Relevance reasoning */}
      {insightData.relevance_reasoning && (
        <p className="text-xs text-t-tertiary">
          {insightData.relevance_reasoning}
        </p>
      )}

      {/* Related goals */}
      {insightData.related_goals.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {insightData.related_goals.map((goal, i) => (
            <span
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/15 text-blue-400"
            >
              {goal}
            </span>
          ))}
        </div>
      )}

      {/* Suggested actions */}
      {insightData.suggested_actions.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {insightData.suggested_actions.map((action, i) => (
            <button
              key={i}
              type="button"
              onClick={() => handleAction(i)}
              disabled={acting !== null}
              className="text-xs px-3 py-1.5 rounded-md bg-violet-500/20 text-violet-300 hover:bg-violet-500/30 transition-colors disabled:opacity-50"
            >
              {acting === i ? "Starting..." : action.description}
            </button>
          ))}
        </div>
      )}

      {/* Dismiss */}
      {insightData.dismiss_available && (
        <div className="pt-1 border-t border-b-primary">
          <button
            type="button"
            onClick={handleDismiss}
            disabled={dismissing}
            className="text-[10px] text-t-tertiary hover:text-t-secondary transition-colors disabled:opacity-50"
          >
            {dismissing ? "Dismissing..." : "Dismiss"}
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/a2ui/components/insight-surface.tsx
git commit -m "feat(spec4b): add InsightSurface frontend component"
```

---

## Task 9: Wire Frontend — Surface Card + Store + Page

**Files:**
- Modify: `frontend/src/components/workspace/surface-card.tsx`
- Modify: `frontend/src/stores/surface-store.ts`
- Modify: `frontend/src/app/page.tsx`

- [ ] **Step 1: Add `proactive_insight` rendering to `surface-card.tsx`**

In `frontend/src/components/workspace/surface-card.tsx`, add the border color for `proactive_insight`:

```typescript
const kindBorderColor: Record<string, string> = {
  plan: "border-l-blue-500",
  approval: "border-l-amber-500",
  briefing: "border-l-green-500",
  alert: "border-l-red-500",
  summary: "border-l-gray-400",
  recommendation: "border-l-gray-400",
  proactive_insight: "border-l-violet-500",
};
```

Add `proposal` to `statusDotColor`:

```typescript
const statusDotColor: Record<string, string> = {
  pending: "bg-gray-400",
  running: "bg-blue-400 animate-pulse",
  completed: "bg-green-400",
  failed: "bg-red-400",
  awaiting_approval: "bg-amber-400",
  cancelled: "bg-gray-500",
  proposal: "bg-violet-400 animate-pulse",
};
```

And add `proposal` to `phaseDotColor`:

```typescript
const phaseDotColor: Record<string, string> = {
  planning: "bg-blue-400 animate-pulse",
  plan_ready: "bg-blue-400",
  executing: "bg-blue-400 animate-pulse",
  approval_needed: "bg-amber-400 animate-pulse",
  completed: "bg-green-400",
  failed: "bg-red-400",
  partial: "bg-amber-400",
  proposal: "bg-violet-400 animate-pulse",
};
```

Then add the insight surface content rendering. Add the import at the top:

```typescript
import { InsightSurface } from "@/components/a2ui/components/insight-surface";
import type { InsightData } from "@/lib/a2ui-types";
```

In the `SurfaceCard` component body, after the subtitle rendering and before the execution step count section, add:

```tsx
      {/* Insight surface content */}
      {kind === "proactive_insight" && surface.insight_data && (
        <div className="mb-2" onClick={(e) => e.stopPropagation()}>
          <InsightSurface
            surfaceId={surface.id}
            insightData={surface.insight_data as InsightData}
          />
        </div>
      )}
```

- [ ] **Step 2: Add `insight_data` field to `WorkspaceSurface` in surface-store.ts**

In `frontend/src/stores/surface-store.ts`, add to the `WorkspaceSurface` interface (after `results`):

```typescript
  // Insight surface fields
  insight_data?: Record<string, unknown> | null;
```

Add a `transitionToExecution` action to the store interface:

```typescript
  transitionToExecution: (surfaceId: string) => void;
```

And implement it in the store:

```typescript
  transitionToExecution: (surfaceId) =>
    set((s) => {
      const idx = s.surfaces.findIndex((sf) => sf.id === surfaceId);
      if (idx === -1) return s;
      const next = [...s.surfaces];
      next[idx] = {
        ...next[idx],
        phase: "planning",
        insight_data: null,
      };
      return { surfaces: next };
    }),
```

- [ ] **Step 3: Include `proposal` phase in active surface sort in `page.tsx`**

In `frontend/src/app/page.tsx`, find the `isActive` function (line 66):

```typescript
    const isActive = (s: WorkspaceSurface) =>
      s.phase === "executing" || s.phase === "approval_needed" || s.phase === "planning";
```

Replace with:

```typescript
    const isActive = (s: WorkspaceSurface) =>
      s.phase === "executing" ||
      s.phase === "approval_needed" ||
      s.phase === "planning" ||
      s.kind === "proactive_insight";
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/workspace/surface-card.tsx frontend/src/stores/surface-store.ts frontend/src/app/page.tsx
git commit -m "feat(spec4b): wire insight surfaces into workspace grid with dismiss + actions"
```

---

## Task 10: Integration Test + Final Verification

**Files:**
- Modify: `backend/tests/test_insight_surfaces.py` (add integration tests)

- [ ] **Step 1: Add integration-style tests**

Append to `backend/tests/test_insight_surfaces.py`:

```python
@pytest.mark.asyncio
async def test_engagement_suppression_integration():
    """Full flow: 5 dismissals → suppression → engagement unsuppresses."""
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    from src.models.engagement_history import EngagementHistory

    row = EngagementHistory(
        workspace_id="ws_test",
        signal_source="gmail",
        signal_category="reply",
    )

    result_mock = MagicMock()
    # First 5 calls return None (creates new row), then return the row
    call_count = 0

    def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        mock_result = MagicMock()
        if call_count <= 1:
            mock_result.scalar_one_or_none.return_value = None
        else:
            mock_result.scalar_one_or_none.return_value = row
        return mock_result

    db.execute = AsyncMock(side_effect=mock_execute)

    from src.services.engagement_service import EngagementService

    svc = EngagementService(db, "ws_test")

    # First engagement creates the row
    await svc.record_engagement("gmail", "reply", "engaged")
    db.add.assert_called_once()

    # Dismiss 5 times
    for _ in range(5):
        await svc.record_engagement("gmail", "reply", "dismissed")

    assert row.dismissed_count == 5
    assert row.consecutive_dismissals == 5
    assert row.suppressed is True

    # Engage again → unsuppress
    await svc.record_engagement("gmail", "reply", "engaged")
    assert row.suppressed is False
    assert row.consecutive_dismissals == 0


def test_dismiss_response_model():
    """DismissResponse validates correctly."""
    from src.api.routes_insights import DismissResponse

    resp = DismissResponse(surface_id="surf_test123")
    assert resp.status == "dismissed"
    assert resp.surface_id == "surf_test123"
```

- [ ] **Step 2: Run full test suite**

Run: `cd backend && python -m pytest tests/test_insight_surfaces.py tests/test_engagement_service.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Run linting**

Run: `cd backend && ruff check src/services/engagement_service.py src/api/routes_insights.py src/orchestrator/contracts.py src/models/engagement_history.py`
Expected: No errors.

- [ ] **Step 4: Run frontend type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_insight_surfaces.py
git commit -m "test(spec4b): add integration tests for insight surface lifecycle"
```

---

## Spec Coverage Checklist

| Spec Component | Task |
|---|---|
| 1. Insight Surface Type (`proactive_insight` kind + `InsightSurfaceData`) | Task 3 |
| 2. Insight Surface Push (`_push_insight_surface()` + Redis→WS + persist) | Task 4 |
| 3. Proposal → Execution Bridge (WS action handler → PlanOutput → execute) | Task 6 |
| 4. Dismissal Learning (`EngagementHistory` model + suppression rules) | Task 1, Task 2 |
| 5. Dismiss API (`POST /v1/insights/{surface_id}/dismiss`) | Task 6 |
| 6. Frontend Insight Components (insight-surface.tsx + surface-card integration) | Task 8, Task 9 |
| Engagement context fed to relevance assessor | Task 3, Task 4 |
| Suppressed signals skip LLM assessment | Task 4 |
| Insight surfaces in workspace rebuild on reconnect | Task 5 |

---

Plan complete and saved to `docs/superpowers/plans/2026-04-11-proactive-insight-surfaces.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
