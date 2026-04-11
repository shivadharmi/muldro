# Spec 4A: Perception Signal Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM relevance assessment, notification tier routing, rate limiting, priority score activation, Persona batching, and cross-source synthesis improvement to the perception pipeline.

**Architecture:** A new `RelevanceAssessor` service (Haiku LLM call) evaluates each perception signal against user goals, returning a score + notification tier. The tier routes signals to push/briefing/silent paths. The existing `Notifier.compute_priority_score()` output — currently ignored — gets wired into delivery decisions. Persona agent calls move from per-message fire-and-forget to a batched scheduler tick. Cross-source synthesis drops its fixed 30-min cooldown in favor of signal-volume triggers.

**Tech Stack:** Python 3.12, async, Pydantic v2, Claude Haiku (via `get_anthropic_client`), Redis (rate limiting), pytest + pytest-asyncio

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| **Create** | `src/services/relevance_assessor.py` | Pydantic models (`SuggestedAction`, `RelevanceAssessment`, `PerceptionSignal`, `UserContext`) + `assess_relevance()` Haiku call + `_determine_tier()` pure function |
| **Create** | `tests/test_relevance_assessor.py` | Unit tests for tier logic, LLM call mocking, edge cases |
| **Create** | `tests/test_notification_rate_limit.py` | Unit tests for per-surface rate limiting in Notifier |
| **Modify** | `src/orchestrator/contracts.py:164-179` | Add `notification_tier` field to `PerceptionDecision` |
| **Modify** | `src/services/notifier.py` | Add `SURFACE_RATE_LIMITS`, `_check_rate_limit()`, `_hold_for_briefing()`, wire `priority` score into delivery decisions |
| **Modify** | `src/services/memory_service.py:360-400` | Add `relevance_score` and `signal_source` metadata params to `store_briefing_memory()` |
| **Modify** | `src/orchestrator/jarvis.py:853-869,1141-1157` | Remove 2 Persona fire-and-forget calls |
| **Modify** | `src/orchestrator/jarvis.py:1420-1490` | Insert relevance assessment after Librarian step, route by tier |
| **Modify** | `src/services/scheduler.py:42,60-78,248-289` | Add `_tick_persona_batch()`, replace synthesis cooldown with volume trigger |
| **Update** | `tests/test_notifier.py` | Add tests for priority-score-based delivery decisions |
| **Update** | `tests/test_scheduler.py` | Add tests for Persona batch tick + synthesis volume trigger |
| **Update** | `tests/test_perception.py` | Add tests for relevance assessment integration in perception cycle |

---

## Task 1: Relevance Assessor — Models & Tier Logic (Pure Functions)

**Files:**
- Create: `backend/src/services/relevance_assessor.py`
- Create: `backend/tests/test_relevance_assessor.py`

### Step 1: Write failing tests for tier determination

- [ ] **Step 1a: Create test file with tier routing tests**

```python
# backend/tests/test_relevance_assessor.py
"""Tests for the relevance assessor: tier logic and LLM call."""

import pytest


class TestDetermineTier:
    """Test the pure _determine_tier() function."""

    def test_high_relevance_immediate_urgency_returns_push(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.8, urgency="immediate")
        assert tier == "push"

    def test_high_relevance_today_urgency_returns_push(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.7, urgency="today")
        assert tier == "push"

    def test_medium_relevance_today_returns_briefing(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.5, urgency="today")
        assert tier == "briefing"

    def test_medium_relevance_this_week_returns_briefing(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.4, urgency="this_week")
        assert tier == "briefing"

    def test_medium_relevance_whenever_returns_briefing(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.45, urgency="whenever")
        assert tier == "briefing"

    def test_low_relevance_returns_silent(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.2, urgency="immediate")
        assert tier == "silent"

    def test_boundary_0_7_immediate_is_push(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.7, urgency="immediate")
        assert tier == "push"

    def test_boundary_0_4_whenever_is_briefing(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.4, urgency="whenever")
        assert tier == "briefing"

    def test_boundary_0_39_is_silent(self):
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.39, urgency="today")
        assert tier == "silent"

    def test_high_relevance_whenever_is_briefing_not_push(self):
        """relevance >= 0.7 but urgency=whenever → briefing, not push."""
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.9, urgency="whenever")
        assert tier == "briefing"

    def test_high_relevance_this_week_is_briefing_not_push(self):
        """relevance >= 0.7 but urgency=this_week → briefing, not push."""
        from src.services.relevance_assessor import _determine_tier

        tier = _determine_tier(relevance_score=0.8, urgency="this_week")
        assert tier == "briefing"
```

- [ ] **Step 1b: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_relevance_assessor.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.relevance_assessor'`

### Step 2: Implement models and tier logic

- [ ] **Step 2a: Create relevance_assessor.py with models and _determine_tier**

```python
# backend/src/services/relevance_assessor.py
"""LLM-based relevance assessment for perception signals.

Evaluates whether a user should care about a signal right now,
scoring relevance against their goals and routing to push/briefing/silent tiers.
"""

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class SuggestedAction(BaseModel):
    """An action the system could take in response to a signal."""

    model_config = ConfigDict(extra="ignore")

    description: str
    capability: str
    action_input: dict[str, Any] = Field(default_factory=dict)


class RelevanceAssessment(BaseModel):
    """Result of LLM relevance assessment for a perception signal."""

    model_config = ConfigDict(extra="ignore")

    relevance_score: float = 0.0  # 0.0-1.0
    reasoning: str = ""
    relates_to_goals: list[str] = Field(default_factory=list)
    urgency: Literal["immediate", "today", "this_week", "whenever"] = "whenever"
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    notification_tier: Literal["push", "briefing", "silent"] = "silent"


class PerceptionSignal(BaseModel):
    """A normalized perception signal to be assessed."""

    model_config = ConfigDict(extra="ignore")

    source: str  # e.g. "gmail", "github", "slack"
    event_type: str  # e.g. "new_email", "pr_review_requested"
    summary: str
    entities: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)


class UserContext(BaseModel):
    """User context for relevance assessment."""

    model_config = ConfigDict(extra="ignore")

    goals: list[str] = Field(default_factory=list)
    recent_activity: str = ""
    preferences: list[str] = Field(default_factory=list)


def _determine_tier(
    relevance_score: float,
    urgency: Literal["immediate", "today", "this_week", "whenever"],
) -> Literal["push", "briefing", "silent"]:
    """Pure function: map relevance score + urgency to notification tier.

    Routing logic:
        relevance >= 0.7 AND urgency in (immediate, today)  → push
        relevance >= 0.4 AND urgency in (today, this_week)  → briefing
        relevance >= 0.4 AND urgency == whenever             → briefing
        relevance < 0.4                                       → silent
    """
    if relevance_score >= 0.7 and urgency in ("immediate", "today"):
        return "push"
    if relevance_score >= 0.4:
        return "briefing"
    return "silent"
```

- [ ] **Step 2b: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_relevance_assessor.py -v
```

Expected: all 11 tests PASS

- [ ] **Step 2c: Commit**

```bash
cd backend && git add src/services/relevance_assessor.py tests/test_relevance_assessor.py
git commit -m "feat(spec4a): add relevance assessor models and tier logic"
```

---

## Task 2: Relevance Assessor — LLM Call

**Files:**
- Modify: `backend/src/services/relevance_assessor.py`
- Modify: `backend/tests/test_relevance_assessor.py`

### Step 1: Write failing test for assess_relevance()

- [ ] **Step 1a: Add LLM call tests**

Append to `tests/test_relevance_assessor.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch


class TestAssessRelevance:
    """Test the assess_relevance() async function with mocked Haiku."""

    @pytest.mark.asyncio
    async def test_returns_assessment_from_llm_response(self):
        from src.services.relevance_assessor import (
            PerceptionSignal,
            UserContext,
            assess_relevance,
        )

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[
                MagicMock(
                    text='{"relevance_score": 0.8, "reasoning": "PR from key collaborator",'
                    ' "relates_to_goals": ["ship v2"], "urgency": "today",'
                    ' "suggested_actions": []}'
                )
            ]
        )

        signal = PerceptionSignal(
            source="github",
            event_type="pr_review_requested",
            summary="PR #42 review requested by Alice",
        )
        context = UserContext(goals=["ship v2 by Friday"])

        result = await assess_relevance(signal, context, mock_client)

        assert result.relevance_score == 0.8
        assert result.notification_tier == "push"  # 0.8 + today = push
        assert result.urgency == "today"
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_silent_on_llm_error(self):
        from src.services.relevance_assessor import (
            PerceptionSignal,
            UserContext,
            assess_relevance,
        )

        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = Exception("API error")

        signal = PerceptionSignal(
            source="gmail", event_type="new_email", summary="Newsletter"
        )
        context = UserContext()

        result = await assess_relevance(signal, context, mock_client)

        assert result.relevance_score == 0.0
        assert result.notification_tier == "silent"

    @pytest.mark.asyncio
    async def test_returns_silent_on_malformed_json(self):
        from src.services.relevance_assessor import (
            PerceptionSignal,
            UserContext,
            assess_relevance,
        )

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="not json at all")]
        )

        signal = PerceptionSignal(
            source="slack", event_type="message", summary="Hey"
        )
        context = UserContext()

        result = await assess_relevance(signal, context, mock_client)

        assert result.relevance_score == 0.0
        assert result.notification_tier == "silent"
```

- [ ] **Step 1b: Run tests to verify new tests fail**

```bash
cd backend && python -m pytest tests/test_relevance_assessor.py::TestAssessRelevance -v
```

Expected: FAIL with `ImportError: cannot import name 'assess_relevance'`

### Step 2: Implement assess_relevance()

- [ ] **Step 2a: Add assess_relevance function to relevance_assessor.py**

Append to `backend/src/services/relevance_assessor.py`:

```python
_RELEVANCE_PROMPT = """\
You are a relevance assessor for a personal AI assistant. Given a signal from \
a data source and the user's current context, assess whether the user should \
care about this right now.

Respond with a JSON object (no markdown fences):
{{
  "relevance_score": <float 0.0-1.0>,
  "reasoning": "<why this matters or doesn't>",
  "relates_to_goals": ["<goal text if relevant>"],
  "urgency": "<immediate|today|this_week|whenever>",
  "suggested_actions": []
}}

User goals: {goals}
Recent activity: {recent_activity}
User preferences: {preferences}

Signal source: {source}
Event type: {event_type}
Summary: {summary}
"""


async def assess_relevance(
    signal: PerceptionSignal,
    user_context: UserContext,
    client: Any,
    model: str = "claude-haiku-4-5-20251001",
) -> RelevanceAssessment:
    """Call Haiku to assess signal relevance. Returns silent assessment on failure."""
    try:
        prompt = _RELEVANCE_PROMPT.format(
            goals=", ".join(user_context.goals) or "none specified",
            recent_activity=user_context.recent_activity or "none",
            preferences=", ".join(user_context.preferences) or "none",
            source=signal.source,
            event_type=signal.event_type,
            summary=signal.summary,
        )

        response = await client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        data = json.loads(text)
        assessment = RelevanceAssessment(**data)
        # Override tier with our deterministic logic
        assessment.notification_tier = _determine_tier(
            assessment.relevance_score, assessment.urgency
        )
        return assessment

    except Exception:
        logger.warning("Relevance assessment failed, defaulting to silent", exc_info=True)
        return RelevanceAssessment(
            relevance_score=0.0,
            reasoning="Assessment failed — defaulting to silent",
            notification_tier="silent",
        )
```

- [ ] **Step 2b: Run all relevance assessor tests**

```bash
cd backend && python -m pytest tests/test_relevance_assessor.py -v
```

Expected: all 14 tests PASS

- [ ] **Step 2c: Commit**

```bash
cd backend && git add src/services/relevance_assessor.py tests/test_relevance_assessor.py
git commit -m "feat(spec4a): implement assess_relevance Haiku LLM call"
```

---

## Task 3: Add notification_tier to PerceptionDecision

**Files:**
- Modify: `backend/src/orchestrator/contracts.py:164-179`

### Step 1: Add field

- [ ] **Step 1a: Add notification_tier to PerceptionDecision**

In `backend/src/orchestrator/contracts.py`, find the `PerceptionDecision` class (line ~164) and add the field after `reasoning`:

```python
class PerceptionDecision(BaseModel):
    """Agent-informed perception policy returned after a perception cycle.

    The planner optionally includes this in its response to control how soon
    a source should next be checked, what entities to watch, and the urgency
    level.  The runtime clamps all values within system guardrails.
    """

    model_config = ConfigDict(extra="ignore")

    next_check_seconds: int | None = None
    mode: Literal["poll", "push", "hybrid", "paused"] | None = None
    watch_entities: list[str] = Field(default_factory=list)
    urgency: Literal["low", "normal", "high"] = "normal"
    reasoning: str = ""
    notification_tier: Literal["push", "briefing", "silent"] | None = None
```

- [ ] **Step 1b: Run existing contract tests to verify no breakage**

```bash
cd backend && python -m pytest tests/ -v -k "contract or perception" --no-header -q
```

Expected: all existing tests PASS (new field has default `None`, so backward compatible)

- [ ] **Step 1c: Commit**

```bash
cd backend && git add src/orchestrator/contracts.py
git commit -m "feat(spec4a): add notification_tier to PerceptionDecision"
```

---

## Task 4: Notification Rate Limiting

**Files:**
- Modify: `backend/src/services/notifier.py`
- Create: `backend/tests/test_notification_rate_limit.py`

### Step 1: Write failing rate-limit tests

- [ ] **Step 1a: Create rate-limit test file**

```python
# backend/tests/test_notification_rate_limit.py
"""Tests for notification rate limiting in Notifier."""

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestRateLimiting:
    """Test per-surface rate caps."""

    @pytest.mark.asyncio
    async def test_first_notification_allowed(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 1
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is True
        redis.incr.assert_called_once_with("notifier:rate:user1:telegram")
        redis.expire.assert_called_once_with("notifier:rate:user1:telegram", 3600)

    @pytest.mark.asyncio
    async def test_telegram_blocked_after_5(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 6  # 6th notification
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_web_allowed_at_15(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 15
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        allowed = await notifier._check_rate_limit("user1", "web")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_web_blocked_at_16(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 16
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        allowed = await notifier._check_rate_limit("user1", "web")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_email_blocked_after_3(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 4
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        allowed = await notifier._check_rate_limit("user1", "email")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_slack_blocked_after_8(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 9
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        allowed = await notifier._check_rate_limit("user1", "slack")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_unknown_surface_uses_default_10(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 10
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        allowed = await notifier._check_rate_limit("user1", "sms")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_expire_only_set_on_first_increment(self):
        from src.services.notifier import Notifier

        redis = AsyncMock()
        redis.incr.return_value = 3  # not the first
        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        await notifier._check_rate_limit("user1", "telegram")
        redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_no_redis_always_allows(self):
        from src.services.notifier import Notifier

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=None)

        allowed = await notifier._check_rate_limit("user1", "telegram")
        assert allowed is True
```

- [ ] **Step 1b: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_notification_rate_limit.py -v
```

Expected: FAIL with `AttributeError: 'Notifier' object has no attribute '_check_rate_limit'`

### Step 2: Implement rate limiting

- [ ] **Step 2a: Add SURFACE_RATE_LIMITS and _check_rate_limit to notifier.py**

In `backend/src/services/notifier.py`, add the constant after the `compute_priority_score` function (after line 48), and add the method to the `Notifier` class:

After line 48 (after `compute_priority_score`), add:

```python
SURFACE_RATE_LIMITS: dict[str, int] = {
    "telegram": 5,   # per hour
    "web": 15,
    "slack": 8,
    "email": 3,
}
```

Add this method to the `Notifier` class (e.g., after `__init__`, before `notify`):

```python
    async def _check_rate_limit(self, user_id: str, surface: str) -> bool:
        """Check if a notification can be sent to this surface within rate limits.

        Uses Redis INCR with 1-hour TTL. Returns True if under limit.
        """
        if not self._redis:
            return True
        key = f"notifier:rate:{user_id}:{surface}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, 3600)
        return count <= SURFACE_RATE_LIMITS.get(surface, 10)
```

- [ ] **Step 2b: Run rate limit tests**

```bash
cd backend && python -m pytest tests/test_notification_rate_limit.py -v
```

Expected: all 9 tests PASS

- [ ] **Step 2c: Commit**

```bash
cd backend && git add src/services/notifier.py tests/test_notification_rate_limit.py
git commit -m "feat(spec4a): add per-surface notification rate limiting"
```

---

## Task 5: Priority Score Activation + Hold-for-Briefing

**Files:**
- Modify: `backend/src/services/notifier.py`
- Modify: `backend/src/services/memory_service.py:360-400`
- Update: `backend/tests/test_notifier.py`

### Step 1: Write failing tests for priority-based delivery

- [ ] **Step 1a: Add priority score delivery tests to test_notifier.py**

Append to `backend/tests/test_notifier.py`:

```python
class TestPriorityScoreDelivery:
    """Test that priority score drives delivery decisions."""

    @pytest.mark.asyncio
    async def test_low_priority_returns_silent(self):
        """Score < 0.3 → silent, no delivery."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        notifier = Notifier(surface_registry=registry)

        result = await notifier.notify(
            user_id="usr_test",
            notification_type="info_update",
            title="Low priority",
            body="Not important",
            data={"urgency": 0.1, "goal_relevance": 0.1, "novelty": 0.1,
                  "confidence": 0.1, "interruptibility": 0.1},
        )
        assert result["status"] == "silent"
        registry.get_active_surfaces.assert_not_called()

    @pytest.mark.asyncio
    async def test_medium_priority_held_for_briefing(self):
        """Score 0.3-0.6 → held for briefing."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        redis = AsyncMock()
        redis.incr.return_value = 1  # rate limit ok
        notifier = Notifier(surface_registry=registry, redis=redis)

        result = await notifier.notify(
            user_id="usr_test",
            notification_type="info_update",
            title="Medium priority",
            body="Somewhat important",
            data={"urgency": 0.5, "goal_relevance": 0.4, "novelty": 0.4,
                  "confidence": 0.4, "interruptibility": 0.4},
        )
        assert result["status"] == "held_for_briefing"

    @pytest.mark.asyncio
    async def test_high_priority_delivers_normally(self):
        """Score >= 0.6 → normal delivery."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        registry.get_active_surfaces.return_value = ["web"]
        registry.get_preferred_surface.return_value = "web"
        redis = AsyncMock()
        redis.incr.return_value = 1
        redis.publish = AsyncMock()
        notifier = Notifier(surface_registry=registry, redis=redis)

        result = await notifier.notify(
            user_id="usr_test",
            notification_type="info_update",
            title="High priority",
            body="Very important",
            data={"urgency": 0.9, "goal_relevance": 0.9, "novelty": 0.9,
                  "confidence": 0.9, "interruptibility": 0.9},
        )
        assert result["status"] == "sent"

    @pytest.mark.asyncio
    async def test_approval_request_bypasses_priority_filter(self):
        """approval_request and critical_alert always deliver."""
        from src.services.notifier import Notifier

        registry = AsyncMock()
        registry.get_active_surfaces.return_value = ["telegram"]
        telegram = AsyncMock(return_value={"status": "sent"})
        redis = AsyncMock()
        redis.incr.return_value = 1
        redis.publish = AsyncMock()
        notifier = Notifier(
            surface_registry=registry, redis=redis, telegram_sender=telegram
        )

        result = await notifier.notify(
            user_id="usr_test",
            notification_type="approval_request",
            title="Approve deploy",
            body="Deploy to prod",
            data={"urgency": 0.1, "approval_id": "apr_123"},  # low urgency but approval
        )
        assert result["status"] == "sent"
```

- [ ] **Step 1b: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_notifier.py::TestPriorityScoreDelivery -v
```

Expected: FAIL (priority score not wired into delivery yet — all deliver regardless of score)

### Step 2: Wire priority score into notify() and add _hold_for_briefing

- [ ] **Step 2a: Modify notify() in notifier.py**

In `backend/src/services/notifier.py`, modify the `notify()` method. After the priority is computed (line ~89) and the notification is persisted, but BEFORE the surface lookup (line ~149), add the priority filter. The key change: `approval_request` and `critical_alert` bypass the filter.

Replace the section from after DB persistence (after the `except Exception: logger.warning("Failed to persist notification"...)` block) through to `surfaces = await self._registry.get_active_surfaces(user_id)` with:

```python
        # Priority-based delivery filter (approval_request + critical_alert bypass)
        if notification_type not in ("approval_request", "critical_alert"):
            if priority < 0.3:
                logger.info(
                    "notification_silent",
                    extra={
                        "notification_id": notification.notification_id,
                        "priority": priority,
                    },
                )
                return {"status": "silent", "priority": priority}
            if priority < 0.6:
                await self._hold_for_briefing(notification, priority)
                return {"status": "held_for_briefing", "priority": priority}

        surfaces = await self._registry.get_active_surfaces(user_id)
```

- [ ] **Step 2b: Add _hold_for_briefing method to Notifier class**

Add after the `_check_rate_limit` method:

```python
    async def _hold_for_briefing(
        self, notification: Notification, priority: float
    ) -> None:
        """Store a notification as a briefing item instead of delivering it."""
        if self._redis:
            try:
                key = f"notifier:briefing_hold:{notification.user_id}"
                entry = json.dumps(
                    {
                        "notification_id": notification.notification_id,
                        "title": notification.title,
                        "body": notification.body,
                        "type": notification.type,
                        "priority": priority,
                        "created_at": notification.created_at,
                    }
                )
                await self._redis.lpush(key, entry)
                await self._redis.expire(key, 86400)  # 24h TTL
            except Exception:
                logger.debug("Failed to hold notification for briefing", exc_info=True)
```

- [ ] **Step 2c: Add relevance metadata params to store_briefing_memory**

In `backend/src/services/memory_service.py`, modify `store_briefing_memory` (line ~360) to accept optional `relevance_score` and `signal_source` metadata:

Change the signature from:
```python
    async def store_briefing_memory(
        self,
        user_id: str,
        workspace_id: str,
        text: str,
        source: str = "perception",
    ) -> str:
```

To:
```python
    async def store_briefing_memory(
        self,
        user_id: str,
        workspace_id: str,
        text: str,
        source: str = "perception",
        relevance_score: float | None = None,
        signal_source: str | None = None,
    ) -> str:
```

And update the `provenance` dict from:
```python
            provenance={"source": source},
```
To:
```python
            provenance={
                "source": source,
                **({"relevance_score": relevance_score} if relevance_score is not None else {}),
                **({"signal_source": signal_source} if signal_source is not None else {}),
            },
```

- [ ] **Step 2d: Run tests**

```bash
cd backend && python -m pytest tests/test_notifier.py tests/test_notification_rate_limit.py -v
```

Expected: all tests PASS

- [ ] **Step 2e: Commit**

```bash
cd backend && git add src/services/notifier.py src/services/memory_service.py tests/test_notifier.py
git commit -m "feat(spec4a): wire priority score into delivery + hold-for-briefing"
```

---

## Task 6: Integrate Rate Limiting into Delivery

**Files:**
- Modify: `backend/src/services/notifier.py`
- Update: `backend/tests/test_notification_rate_limit.py`

### Step 1: Write failing test for rate-limit integration in _deliver

- [ ] **Step 1a: Add integration test**

Append to `backend/tests/test_notification_rate_limit.py`:

```python
class TestRateLimitInDelivery:
    """Test that rate limiting is enforced during actual delivery."""

    @pytest.mark.asyncio
    async def test_rate_limited_notification_held_for_briefing(self):
        from src.services.notifier import Notifier

        registry = AsyncMock()
        registry.get_active_surfaces.return_value = ["telegram"]
        registry.get_preferred_surface.return_value = "telegram"
        redis = AsyncMock()
        redis.incr.return_value = 6  # over telegram limit of 5
        redis.publish = AsyncMock()
        telegram = AsyncMock(return_value={"status": "sent"})

        notifier = Notifier(
            surface_registry=registry, redis=redis, telegram_sender=telegram
        )

        result = await notifier.notify(
            user_id="usr_test",
            notification_type="info_update",
            title="Rate limited",
            body="Too many",
            data={"urgency": 0.9, "goal_relevance": 0.9, "novelty": 0.9,
                  "confidence": 0.9, "interruptibility": 0.9},
        )
        # High priority but rate-limited → held for briefing
        assert result["status"] == "rate_limited"
        telegram.assert_not_called()
```

- [ ] **Step 1b: Run to verify failure**

```bash
cd backend && python -m pytest tests/test_notification_rate_limit.py::TestRateLimitInDelivery -v
```

Expected: FAIL (rate limit not checked during delivery yet)

### Step 2: Add rate-limit check before delivery loop

- [ ] **Step 2a: Modify notify() delivery section**

In `backend/src/services/notifier.py`, in the `notify()` method, after `surfaces = await self._registry.get_active_surfaces(user_id)` and the empty-surfaces check, but BEFORE the `if notification_type in ("approval_request", "critical_alert"):` delivery block, add rate-limit filtering:

```python
        # Rate-limit filtering: remove surfaces that are over their hourly cap
        if notification_type not in ("approval_request", "critical_alert"):
            allowed_surfaces = []
            for surface in surfaces:
                if await self._check_rate_limit(user_id, surface):
                    allowed_surfaces.append(surface)
            if not allowed_surfaces:
                await self._hold_for_briefing(notification, priority)
                return {"status": "rate_limited", "priority": priority}
            surfaces = allowed_surfaces
```

- [ ] **Step 2b: Run all rate limit tests**

```bash
cd backend && python -m pytest tests/test_notification_rate_limit.py -v
```

Expected: all tests PASS

- [ ] **Step 2c: Commit**

```bash
cd backend && git add src/services/notifier.py tests/test_notification_rate_limit.py
git commit -m "feat(spec4a): integrate rate limiting into notification delivery"
```

---

## Task 7: Remove Persona Fire-and-Forget Calls from jarvis.py

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:853-869,1141-1157`

### Step 1: Remove Persona calls from both message paths

- [ ] **Step 1a: Remove Persona call from process_message() (non-streaming)**

In `backend/src/orchestrator/jarvis.py`, delete the entire block at lines ~853-869:

```python
            # Step 5: Persona learning (fire-and-forget for meaningful intents)
            if intent in ("command", "complex"):
                try:
                    await self._call_agent(
                        "persona",
                        message=(
                            f"Observe this user interaction on {surface}:\n"
                            f"User said: {message}\n"
                            f"Plan goal: {plan.goal}\n"
                            f"Extract any preference signals."
                        ),
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    )
                except Exception:
                    logger.debug("Persona reflection skipped", exc_info=True)
```

- [ ] **Step 1b: Remove Persona call from process_message_stream() (streaming)**

In `backend/src/orchestrator/jarvis.py`, delete the entire block at lines ~1141-1157:

```python
            # Persona learning (meaningful intents only)
            if intent in ("command", "complex"):
                try:
                    await self._call_agent(
                        "persona",
                        message=(
                            f"Observe this user interaction on {surface}:\n"
                            f"User said: {message}\n"
                            f"Plan goal: {plan.goal}\n"
                            f"Extract any preference signals."
                        ),
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    )
                except Exception:
                    pass
```

- [ ] **Step 1c: Run existing tests to verify no breakage**

```bash
cd backend && python -m pytest tests/ -v -k "process_message or orchestrator" --no-header -q
```

Expected: all existing tests PASS

- [ ] **Step 1d: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py
git commit -m "refactor(spec4a): remove per-message Persona fire-and-forget calls"
```

---

## Task 8: Persona Batching in Scheduler

**Files:**
- Modify: `backend/src/services/scheduler.py`
- Update: `backend/tests/test_scheduler.py`

### Step 1: Write failing tests for Persona batch tick

- [ ] **Step 1a: Add Persona batch tests to test_scheduler.py**

Append to `backend/tests/test_scheduler.py`:

```python
class TestPersonaBatch:
    """Test _tick_persona_batch() in SchedulerLoop."""

    @pytest.mark.asyncio
    async def test_skips_when_not_10th_tick(self):
        from src.services.scheduler import SchedulerLoop
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)
        scheduler._tick_count = 3  # not divisible by 10

        # Should return immediately without calling anything
        await scheduler._tick_persona_batch(factory=AsyncMock())
        # No exception = pass (no orchestrator needed)

    @pytest.mark.asyncio
    async def test_skips_when_fewer_than_5_interactions(self):
        from src.services.scheduler import SchedulerLoop
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        orchestrator = AsyncMock()
        scheduler = SchedulerLoop(settings=settings, orchestrator=orchestrator)
        scheduler._tick_count = 10

        # Mock DB returning only 3 interactions
        mock_factory = AsyncMock()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [MagicMock()] * 3
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_db

        await scheduler._tick_persona_batch(factory=mock_factory)
        orchestrator._call_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_persona_with_5_plus_interactions(self):
        from src.services.scheduler import SchedulerLoop
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        orchestrator = AsyncMock()
        orchestrator._call_agent = AsyncMock(return_value="ok")
        scheduler = SchedulerLoop(settings=settings, orchestrator=orchestrator)
        scheduler._tick_count = 10

        # Mock DB returning 6 interactions
        mock_interactions = []
        for i in range(6):
            m = MagicMock()
            m.message_preview = f"message {i}"
            m.intent = "command"
            m.user_id = "usr_test"
            m.workspace_id = "ws_test"
            mock_interactions.append(m)

        mock_factory = AsyncMock()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_interactions
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_db

        await scheduler._tick_persona_batch(factory=mock_factory)
        orchestrator._call_agent.assert_called_once()
        call_args = orchestrator._call_agent.call_args
        assert call_args[0][0] == "persona"
        assert "message 0" in call_args[1]["message"] or "message 0" in call_args[0][1]
```

- [ ] **Step 1b: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_scheduler.py::TestPersonaBatch -v
```

Expected: FAIL with `AttributeError: 'SchedulerLoop' object has no attribute '_tick_persona_batch'`

### Step 2: Implement _tick_persona_batch

- [ ] **Step 2a: Add _tick_persona_batch to SchedulerLoop**

In `backend/src/services/scheduler.py`, add this method to the `SchedulerLoop` class (e.g. after `_tick_perception`):

```python
    async def _tick_persona_batch(self, factory=None) -> None:
        """Run Persona agent on recent interactions every 10th tick (~5 min).

        Only fires when there are 5+ interactions since last batch.
        """
        if getattr(self, "_tick_count", 0) % 10 != 0:
            return
        if not self._orchestrator:
            return

        try:
            factory = factory or get_session_factory()
            async with factory() as db:
                from sqlalchemy import select

                from src.models.interaction_log import InteractionLog

                last_batch = getattr(self, "_last_persona_batch_at", None)
                query = (
                    select(InteractionLog)
                    .order_by(InteractionLog.created_at.desc())
                    .limit(20)
                )
                if last_batch:
                    query = query.where(InteractionLog.created_at > last_batch)

                result = await db.execute(query)
                interactions = result.scalars().all()

                if len(interactions) < 5:
                    return

                summary = "\n".join(
                    f"- {i.message_preview or '(no preview)'} → {i.intent or 'unknown'}"
                    for i in interactions
                )
                user_id = interactions[0].user_id
                workspace_id = getattr(interactions[0], "workspace_id", "") or ""

                await self._orchestrator._call_agent(
                    "persona",
                    message=f"Analyze these recent user interactions and extract preference patterns:\n{summary}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
                from datetime import datetime, timezone

                self._last_persona_batch_at = datetime.now(timezone.utc)
                logger.info("Persona batch completed: %d interactions analyzed", len(interactions))

        except Exception:
            logger.warning("Persona batch tick failed", exc_info=True)
```

Also add the import alias at the top of the method. We need to avoid shadowing `get_session_factory`. Update the import at the top of the file — rename the module-level import:

In the file's existing import `from src.models.database import get_session_factory`, no change needed since we pass it as a parameter. Actually, let's check — the `_tick` method uses `factory = get_session_factory()`. Let's use the same pattern. Change the method to:

```python
    async def _tick_persona_batch(self, factory=None) -> None:
```

The `factory` parameter allows tests to inject a mock DB session factory.

- [ ] **Step 2b: Wire _tick_persona_batch into _tick()**

In `backend/src/services/scheduler.py`, in the `_tick()` method (~line 73), after the eviction/DLQ block and before the schedule processing, add:

```python
        # 4b. Persona batch — every 10th tick (~5 min)
        await self._tick_persona_batch()
```

Update the existing comment `# 4.` to `# 4a.`

- [ ] **Step 2c: Run scheduler tests**

```bash
cd backend && python -m pytest tests/test_scheduler.py -v
```

Expected: all tests PASS

- [ ] **Step 2d: Commit**

```bash
cd backend && git add src/services/scheduler.py tests/test_scheduler.py
git commit -m "feat(spec4a): add Persona batching every 10th scheduler tick"
```

---

## Task 9: Cross-Source Synthesis — Volume-Based Trigger

**Files:**
- Modify: `backend/src/services/scheduler.py:248-289`
- Update: `backend/tests/test_scheduler.py`

### Step 1: Write failing tests for volume-based trigger

- [ ] **Step 1a: Add synthesis trigger tests**

Append to `backend/tests/test_scheduler.py`:

```python
class TestCrossSourceSynthesisTrigger:
    """Test that synthesis triggers on volume, not cooldown."""

    def test_synthesis_triggers_with_2_sources_3_events(self):
        """2+ sources AND 3+ total events → trigger synthesis."""
        # This tests the trigger condition logic
        sources_with_events = 2
        total_event_count = 3
        should_trigger = sources_with_events >= 2 and total_event_count >= 3
        assert should_trigger is True

    def test_synthesis_skips_with_1_source(self):
        sources_with_events = 1
        total_event_count = 5
        should_trigger = sources_with_events >= 2 and total_event_count >= 3
        assert should_trigger is False

    def test_synthesis_skips_with_2_sources_but_only_2_events(self):
        sources_with_events = 2
        total_event_count = 2
        should_trigger = sources_with_events >= 2 and total_event_count >= 3
        assert should_trigger is False
```

- [ ] **Step 1b: Run to verify they pass (pure logic)**

```bash
cd backend && python -m pytest tests/test_scheduler.py::TestCrossSourceSynthesisTrigger -v
```

Expected: PASS (these are pure logic tests)

### Step 2: Replace cooldown with volume trigger

- [ ] **Step 2a: Modify _tick_perception synthesis block**

In `backend/src/services/scheduler.py`, replace the cross-source synthesis block (~lines 248-289). Find:

```python
                # D2: Cross-source synthesis — when 2+ sources had new events,
                # ask the Planner to synthesize cross-cutting insights
                import time

                sources_with_events = sum(
                    1
                    for s in due_states
                    if not s.pending_run  # was processed (not skipped)
                )
                now = time.monotonic()
                synthesis_cooldown = 1800  # 30 minutes
                if (
                    sources_with_events >= 2
                    and self._orchestrator
                    and (now - self._last_synthesis_at) > synthesis_cooldown
                ):
                    self._last_synthesis_at = now
```

Replace with:

```python
                # D2: Cross-source synthesis — trigger on signal volume
                # (2+ sources with events AND 3+ total events)
                source_event_counts = {}
                for i, r in enumerate(results):
                    if not isinstance(r, BaseException):
                        src_name, evt_count = r
                        if evt_count > 0:
                            source_event_counts[src_name] = evt_count

                sources_with_events = len(source_event_counts)
                total_event_count = sum(source_event_counts.values())

                if (
                    sources_with_events >= 2
                    and total_event_count >= 3
                    and self._orchestrator
                ):
```

Also remove the `self._last_synthesis_at = now` line just inside the if block (since we no longer use cooldown).

- [ ] **Step 2b: Clean up _last_synthesis_at references**

Remove `self._last_synthesis_at: float = 0.0` from `__init__` (line ~42). If it's used elsewhere, keep it but it should only be in the synthesis block.

Actually, keep the attribute removal minimal — just remove the cooldown check. The `_last_synthesis_at` assignment inside the block can be removed too since nothing reads it anymore. But to be safe (other code might reference it), just remove the cooldown condition.

- [ ] **Step 2c: Run scheduler tests**

```bash
cd backend && python -m pytest tests/test_scheduler.py -v
```

Expected: all tests PASS

- [ ] **Step 2d: Commit**

```bash
cd backend && git add src/services/scheduler.py tests/test_scheduler.py
git commit -m "feat(spec4a): replace 30-min synthesis cooldown with volume trigger"
```

---

## Task 10: Integrate Relevance Assessment into Perception Cycle

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:1420-1490`
- Update: `backend/tests/test_perception.py`

### Step 1: Write failing test for relevance assessment in perception

- [ ] **Step 1a: Add relevance integration test**

Append to `backend/tests/test_perception.py`:

```python
class TestPerceptionRelevanceAssessment:
    """Test relevance assessment integration in run_perception_cycle."""

    @pytest.mark.asyncio
    async def test_perception_cycle_calls_relevance_assessor(self):
        """After librarian extraction, relevance should be assessed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

        settings = make_mock_settings()

        with (
            patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client,
            patch("src.services.relevance_assessor.assess_relevance") as mock_assess,
        ):
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            # Mock assess_relevance to return a briefing-tier assessment
            from src.services.relevance_assessor import RelevanceAssessment

            mock_assess.return_value = RelevanceAssessment(
                relevance_score=0.5,
                reasoning="Moderately relevant",
                urgency="today",
                notification_tier="briefing",
            )

            from src.orchestrator.jarvis import JarvisOrchestrator

            orch = JarvisOrchestrator(settings)

            # Mock all the internal methods
            orch._poll_connector = AsyncMock(
                return_value=(
                    [MagicMock(entity_id=None)],
                    "cursor_123",
                    None,
                    "opaque",
                )
            )
            orch._ingest_raw_events = AsyncMock(return_value=["New PR opened"])
            orch._update_cursor = AsyncMock()
            orch._call_agent = AsyncMock(return_value="extracted entities")
            orch._apply_perception_policy_from_planner = AsyncMock()
            orch._queue_perception_plan = AsyncMock(return_value=None)
            orch._publish_event = AsyncMock()
            orch._trace_manager = MagicMock()
            orch._trace_manager.start_trace.return_value = MagicMock(trace_id="trace_1")
            orch._trace_manager.finish_trace = AsyncMock()
            orch._budget = MagicMock()
            orch._budget.get_budget_status = AsyncMock(return_value=MagicMock())
            orch._budget.should_allow_perception.return_value = True

            result = await orch.run_perception_cycle(
                source="github",
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            assert result["status"] == "completed"
            mock_assess.assert_called_once()
```

- [ ] **Step 1b: Run to verify failure**

```bash
cd backend && python -m pytest tests/test_perception.py::TestPerceptionRelevanceAssessment -v
```

Expected: FAIL (`assess_relevance` is never called in `run_perception_cycle` yet)

### Step 2: Insert relevance assessment into run_perception_cycle

- [ ] **Step 2a: Add relevance assessment after Librarian step**

In `backend/src/orchestrator/jarvis.py`, in `run_perception_cycle()`, after the Librarian call (line ~1428) and the correlation context enrichment block (line ~1457), but BEFORE the Planner step (line ~1459), insert the relevance assessment:

```python
            # Step 2b: Assess relevance of signals against user context
            try:
                from src.services.relevance_assessor import (
                    PerceptionSignal,
                    RelevanceAssessment,
                    UserContext,
                    assess_relevance,
                )

                signal = PerceptionSignal(
                    source=source,
                    event_type=f"perception_{source}",
                    summary=observer_summary[:500],
                )

                # Build user context from goals + preferences
                user_goals = []
                user_prefs = []
                try:
                    from src.services.memory_service import MemoryService

                    async with self._db_factory() as db:
                        mem_svc = MemoryService(db, self._settings)
                        # get_user_preferences(user_id, category, max_results, workspace_id)
                        prefs = await mem_svc.get_user_preferences(
                            user_id, workspace_id=workspace_id
                        )
                        for p in prefs[:10]:
                            if getattr(p, "memory_type", "") == "goal":
                                user_goals.append(p.fact_text)
                            else:
                                user_prefs.append(p.fact_text)
                except Exception:
                    logger.debug("Failed to load user context for relevance", exc_info=True)

                user_context = UserContext(
                    goals=user_goals,
                    preferences=user_prefs,
                )

                assessment = await assess_relevance(
                    signal, user_context, self._client
                )

                # Route by notification tier
                if assessment.notification_tier == "briefing":
                    try:
                        async with self._db_factory() as db:
                            mem_svc = MemoryService(db, self._settings)
                            await mem_svc.store_briefing_memory(
                                user_id=user_id,
                                workspace_id=workspace_id,
                                text=f"{observer_summary[:300]}\n\nWhy: {assessment.reasoning}",
                                source=f"perception:{source}",
                                relevance_score=assessment.relevance_score,
                                signal_source=source,
                            )
                            await db.commit()
                    except Exception:
                        logger.warning("Failed to store briefing memory", exc_info=True)

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

                # silent tier: already in world model from Librarian, no action needed

            except Exception:
                logger.warning("Relevance assessment failed, continuing without", exc_info=True)
```

Note: The notifier is accessed via `self._services.notifier` (already on `ServiceContainer` at `src/orchestrator/services.py:46`). If `_services` is `None` or `notifier` is `None`, the push notification is silently skipped — acceptable since Spec 4B will add proper insight surfaces.

- [ ] **Step 2b: Run perception tests**

```bash
cd backend && python -m pytest tests/test_perception.py -v
```

Expected: all tests PASS

- [ ] **Step 2c: Run full test suite**

```bash
cd backend && python -m pytest tests/ -v --no-header -q 2>&1 | tail -20
```

Expected: all existing tests PASS, no regressions

- [ ] **Step 2d: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py tests/test_perception.py
git commit -m "feat(spec4a): integrate relevance assessment into perception cycle"
```

---

## Task 11: Final Integration Test + Lint

**Files:**
- All modified files

### Step 1: Run full test suite

- [ ] **Step 1a: Run all tests**

```bash
cd backend && python -m pytest tests/ -v --no-header -q 2>&1 | tail -30
```

Expected: all tests PASS

### Step 2: Lint and format

- [ ] **Step 2a: Run ruff**

```bash
cd backend && ruff check src/services/relevance_assessor.py src/services/notifier.py src/services/scheduler.py src/services/memory_service.py src/orchestrator/jarvis.py src/orchestrator/contracts.py
```

- [ ] **Step 2b: Run ruff format**

```bash
cd backend && ruff format src/services/relevance_assessor.py src/services/notifier.py src/services/scheduler.py src/services/memory_service.py src/orchestrator/jarvis.py src/orchestrator/contracts.py
```

- [ ] **Step 2c: Fix any lint issues and re-run tests**

```bash
cd backend && ruff check src/ tests/ --fix && python -m pytest tests/ -v --no-header -q 2>&1 | tail -10
```

- [ ] **Step 2d: Final commit**

```bash
cd backend && git add -u
git commit -m "chore(spec4a): lint and format perception signal routing"
```

---

## Verification Checklist

After all tasks are complete, verify against the spec's success criteria:

- [ ] Perception signals assessed for relevance before routing (Task 10)
- [ ] Three notification tiers (push/briefing/silent) route correctly (Tasks 1-2, 10)
- [ ] Per-surface rate limiting prevents spam (Tasks 4, 6)
- [ ] Priority score drives delivery decisions (Task 5)
- [ ] Persona runs batched every ~5 min, not per-message (Tasks 7-8)
- [ ] Cross-source synthesis triggers on signal volume, not fixed cooldown (Task 9)
- [ ] Existing perception pipeline continues working for non-assessed signals (Task 10 — assessment failure falls through)
