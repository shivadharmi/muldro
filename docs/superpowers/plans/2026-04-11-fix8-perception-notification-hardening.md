# Fix-8: Perception & Notification Hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the perception and notification subsystems — fix rate-limit atomicity, engagement tracking gaps, follow-up re-delivery, hardcoded model IDs, tier routing logic, persona batch unbounded queries, ID conventions, concurrent insert safety, and unbounded in-memory caches.

**Tech Stack:** Python 3.12, ruff (line-length 100), pytest, SQLAlchemy async, Redis, Alembic, ULID

---

## Phase 1: Notification Hardening (HIGH + LOW)

### Task 1: Atomic rate-limit TTL (H-18)

**Files:**
- Modify: `backend/src/services/notifier.py:98-110`

**Problem:** `_check_rate_limit` calls `INCR` then conditionally calls `EXPIRE` only when `count == 1`. If `EXPIRE` fails after `INCR`, the key has no TTL and the rate limit becomes permanent.

- [ ] **Step 1: Replace INCR+EXPIRE with atomic pipeline**

Replace the `_check_rate_limit` method (lines 98-110) with a Redis pipeline that always sets TTL:

```python
async def _check_rate_limit(self, user_id: str, surface: str) -> bool:
    """Check if a notification can be sent to this surface within rate limits.

    Uses Redis pipeline with INCR + EXPIRE (always applied) for atomicity.
    If EXPIRE fails after INCR, the next call will re-apply TTL.
    """
    if not self._redis:
        return True
    key = f"notifier:rate:{user_id}:{surface}"
    pipe = self._redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, 3600)
    results = await pipe.execute()
    count = results[0]
    return count <= SURFACE_RATE_LIMITS.get(surface, 10)
```

Key change: `expire` is called on every increment (idempotent), not just on `count == 1`. This ensures TTL is always set even if a previous `expire` call failed.

- [ ] **Step 2: Add test for atomic rate limiting**

Add to `backend/tests/test_notifier.py`:
- Test that `_check_rate_limit` calls pipeline with both `incr` and `expire`
- Test that rate limit returns False when count exceeds surface limit
- Test that `expire` is called every time (not just first increment)

### Task 2: Bound `_delivered` dict (L-15)

**Files:**
- Modify: `backend/src/services/notifier.py:76`

**Problem:** `self._delivered: dict[str, set[str]]` grows without bound in long-running processes.

- [ ] **Step 1: Add max-size eviction to `_mark_delivered`**

In `__init__` (line 76), keep the existing dict. Then in `_mark_delivered` (line 547-552), add eviction before inserting:

```python
async def _mark_delivered(self, notification_id: str, surface: str) -> None:
    """Mark a notification as delivered on a surface (for dedup)."""
    if self._redis:
        key = f"jarvis:notif_delivered:{notification_id}"
        await self._redis.set(key, surface, ex=86400)  # 24h TTL
    # Evict oldest entries when in-memory cache exceeds limit
    if len(self._delivered) >= 10_000:
        # Remove first ~1000 entries (oldest by insertion order)
        keys_to_remove = list(self._delivered.keys())[:1000]
        for k in keys_to_remove:
            del self._delivered[k]
    self._delivered.setdefault(notification_id, set()).add(surface)
```

Note: This is a secondary dedup cache — Redis is the primary store. Evicting old entries is safe because `is_delivered` checks Redis first.

- [ ] **Step 2: Add test for eviction**

Test that after inserting 10,001 entries, the dict is pruned and new entries still work.

---

## Phase 2: Engagement & Perception (HIGH + MEDIUM)

### Task 3: Track engagement for silent tier (H-19)

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:1513` (the `# silent tier` comment block)

**Problem:** Silent signals bypass `engagement_service.record_engagement()` entirely. The engagement system has no visibility into ignored signals, so suppression thresholds never activate for consistently-silent sources.

- [ ] **Step 1: Add engagement tracking for silent tier**

After line 1513 (`# silent tier: already in world model from Librarian, no action needed`), add engagement tracking. The surrounding code already has `assessment`, `source`, `workspace_id`, and `user_id` in scope. Look at how the `engagement_service` is instantiated earlier in the same `_run_perception_cycle` method (it uses `self._db_factory`):

```python
# silent tier: record in engagement history for suppression tracking
try:
    async with self._db_factory() as db:
        eng_svc = EngagementService(db, workspace_id)
        await eng_svc.record_engagement(
            signal_source=source,
            signal_category=signal.event_type,
            action="ignored",
        )
        await db.commit()
except Exception:
    logger.debug("Failed to record silent engagement", exc_info=True)
```

Ensure the import `from src.services.engagement_service import EngagementService` exists at the top of the file (check if already imported — it likely is since the briefing/push tiers use it).

- [ ] **Step 2: Add test for silent tier engagement tracking**

Mock `_db_factory` and `EngagementService`, verify `record_engagement` is called with `action="ignored"` when `assessment.notification_tier == "silent"`.

### Task 4: Fix `_determine_tier` logical gap (M-23)

**Files:**
- Modify: `backend/src/services/relevance_assessor.py:58-74`

**Problem:** High relevance (>=0.7) combined with `this_week` urgency falls through to `briefing` instead of `push`. A highly relevant signal should push even if urgency is only `this_week`.

- [ ] **Step 1: Expand push tier to include `this_week` for high relevance**

Replace the `_determine_tier` function (lines 58-74):

```python
def _determine_tier(
    relevance_score: float,
    urgency: Literal["immediate", "today", "this_week", "whenever"],
) -> Literal["push", "briefing", "silent"]:
    """Pure function: map relevance score + urgency to notification tier.

    Routing logic:
        relevance >= 0.7 AND urgency in (immediate, today, this_week) → push
        relevance >= 0.4 AND urgency != whenever                       → briefing
        relevance >= 0.4 AND urgency == whenever                       → briefing
        relevance < 0.4                                                 → silent
    """
    if relevance_score >= 0.7 and urgency in ("immediate", "today", "this_week"):
        return "push"
    if relevance_score >= 0.4:
        return "briefing"
    return "silent"
```

The only change is adding `"this_week"` to the push tier condition on line 70.

- [ ] **Step 2: Update existing tests and add new case**

Add test: `_determine_tier(0.8, "this_week")` should return `"push"` (previously returned `"briefing"`). Verify existing tests still pass — `_determine_tier(0.8, "whenever")` should still return `"briefing"`.

### Task 5: Remove hardcoded model ID in relevance_assessor (M-22)

**Files:**
- Modify: `backend/src/services/relevance_assessor.py:104-106`

**Problem:** `"claude-haiku-4-5-20251001"` is hardcoded as a default parameter. Other LLM calls in the codebase resolve via `MODEL_TIERS["haiku"]` / `BEDROCK_MODEL_TIERS["haiku"]` (see `backend/src/orchestrator/jarvis.py:67-74, 248-252`).

- [ ] **Step 1: Import model tier constants and use as default**

At the top of `relevance_assessor.py`, add:

```python
from src.config.settings import get_settings

_HAIKU_MODEL_FALLBACK = "claude-haiku-4-5-20251001"
```

Then update the `assess_relevance` function signature (line 101-106):

```python
async def assess_relevance(
    signal: PerceptionSignal,
    user_context: UserContext,
    client: Any,
    model: str | None = None,
    engagement_context: str = "",
) -> RelevanceAssessment:
    """Call Haiku to assess signal relevance. Returns silent assessment on failure."""
    if model is None:
        try:
            settings = get_settings()
            model = settings.haiku_model if hasattr(settings, "haiku_model") else _HAIKU_MODEL_FALLBACK
        except Exception:
            model = _HAIKU_MODEL_FALLBACK
```

Check the actual settings class (`backend/src/config/settings.py`) for the correct attribute name. If there is no `haiku_model` setting, check how `jarvis.py` resolves it (lines 248-252 use `MODEL_TIERS` dict). Match that pattern:

```python
from src.orchestrator.jarvis import MODEL_TIERS, BEDROCK_MODEL_TIERS

def _get_haiku_model() -> str:
    try:
        settings = get_settings()
        tiers = BEDROCK_MODEL_TIERS if settings.use_bedrock else MODEL_TIERS
        return tiers["haiku"]
    except Exception:
        return "claude-haiku-4-5-20251001"
```

Use whichever approach avoids circular imports. If `MODEL_TIERS` is in `jarvis.py` and importing causes circular deps, extract the tier dicts to a shared module or just use the settings-based approach.

- [ ] **Step 2: Apply same fix to risk_assessor.py**

`backend/src/services/risk_assessor.py:76,122` has the same hardcoded model. Apply the same pattern.

- [ ] **Step 3: Add test verifying model is configurable**

Test that `assess_relevance` accepts a custom `model` parameter and passes it to `client.messages.create`.

---

## Phase 3: Scheduler Fixes (MEDIUM)

### Task 6: Re-deliver follow-up notifications (M-20)

**Files:**
- Modify: `backend/src/services/scheduler.py:627-650`

**Problem:** `_check_follow_ups` resets `follow_up_at=None` and `status="pending"` but nothing picks up these pending notifications for re-delivery.

- [ ] **Step 1: Add `_tick_pending_notifications` method**

Add after `_check_follow_ups` (line 650):

```python
async def _tick_pending_notifications(self, factory) -> None:
    """Deliver pending notifications that were re-queued by _check_follow_ups."""
    try:
        from src.models.notifications import Notification as NotifModel

        async with factory() as db:
            now = datetime.now(timezone.utc)
            # Pick up notifications that are pending and have no follow_up_at
            # (meaning they were reset by _check_follow_ups)
            result = await db.execute(
                select(NotifModel)
                .where(
                    NotifModel.status == "pending",
                    NotifModel.follow_up_at.is_(None),
                    NotifModel.created_at >= now - timedelta(hours=24),
                )
                .limit(10)
            )
            pending = result.scalars().all()
            if not pending or not self._orchestrator:
                return

            notifier = getattr(self._orchestrator, "_notifier", None)
            if not notifier:
                return

            for n in pending:
                try:
                    await notifier.notify(
                        user_id=n.user_id,
                        notification_type=n.channel,
                        title=n.title,
                        body=n.body,
                        data=n.payload_json or {},
                        workspace_id=n.workspace_id or "",
                    )
                    n.status = "sent"
                except Exception:
                    logger.debug(
                        "Failed to re-deliver notification %s",
                        n.notification_id,
                        exc_info=True,
                    )
            await db.commit()
    except Exception:
        logger.debug("Pending notification tick failed", exc_info=True)
```

- [ ] **Step 2: Wire into scheduler tick**

Find the main `_tick` method and add `await self._tick_pending_notifications(factory)` after the `_check_follow_ups` call. Ensure `timedelta` is imported (it likely already is).

- [ ] **Step 3: Add test for follow-up re-delivery**

Test that after `_check_follow_ups` resets notifications to pending, `_tick_pending_notifications` calls `notifier.notify` for each.

### Task 7: Bound first persona batch (M-24)

**Files:**
- Modify: `backend/src/services/scheduler.py:583-586`

**Problem:** When `_last_persona_batch_at` is `None` (first run), the query has no time bound and processes all-time interactions.

- [ ] **Step 1: Default `last_batch` to 24h ago**

Replace lines 583-586:

```python
last_batch = getattr(self, "_last_persona_batch_at", None)
if last_batch is None:
    last_batch = datetime.now(timezone.utc) - timedelta(hours=24)
query = (
    select(InteractionLog)
    .where(InteractionLog.created_at > last_batch)
    .order_by(InteractionLog.created_at.desc())
    .limit(20)
)
```

Note: the `.where` clause is now always applied (not conditional on `last_batch`).

- [ ] **Step 2: Add test for bounded first batch**

Verify that on first tick (no `_last_persona_batch_at`), only interactions from the last 24h are queried.

---

## Phase 4: Data Layer Fixes (LOW)

### Task 8: ULID ID for EngagementHistory (L-13)

**Files:**
- Modify: `backend/src/models/engagement_history.py:27`
- Create: Alembic migration

**Problem:** `EngagementHistory.id` uses `Integer` auto-increment. Project convention is `str` with type prefix + ULID.

- [ ] **Step 1: Update model to use ULID string ID**

Replace line 27:

```python
id: Mapped[str] = mapped_column(
    String(64), primary_key=True, default=lambda: f"eng_{ULID()}"
)
```

Add import at top: `from ulid import ULID`

- [ ] **Step 2: Create Alembic migration**

Run `alembic revision --autogenerate -m "engagement_history id to ulid string"` from `backend/`. If the table has existing data, the migration needs to handle conversion. If the table is new/empty (check migration history), a simple column type change suffices.

- [ ] **Step 3: Verify migration runs cleanly**

Run `alembic upgrade head` and confirm no errors.

### Task 9: Flush after add in `_get_or_create` (L-14)

**Files:**
- Modify: `backend/src/services/engagement_service.py:44-56`

**Problem:** No `flush()` after `self._db.add(row)`. Concurrent calls can both pass the `scalar_one_or_none()` check and both try to insert, causing `IntegrityError`.

- [ ] **Step 1: Add flush + IntegrityError handling**

Replace lines 44-56:

```python
row = EngagementHistory(
    workspace_id=self._workspace_id,
    signal_source=signal_source,
    signal_category=signal_category,
    engaged_count=0,
    dismissed_count=0,
    ignored_count=0,
    consecutive_dismissals=0,
    engagement_rate=0.0,
    suppressed=False,
)
self._db.add(row)
try:
    await self._db.flush()
except IntegrityError:
    await self._db.rollback()
    # Re-query after concurrent insert
    result = await self._db.execute(
        select(EngagementHistory).where(
            EngagementHistory.workspace_id == self._workspace_id,
            EngagementHistory.signal_source == signal_source,
            EngagementHistory.signal_category == signal_category,
        )
    )
    row = result.scalar_one()
return row
```

Add import: `from sqlalchemy.exc import IntegrityError`

- [ ] **Step 2: Add test for concurrent insert handling**

Test that when two concurrent `_get_or_create` calls race, the second one returns the existing row instead of raising.

---

## Verification

After all phases:

```bash
cd backend
ruff check src/services/notifier.py src/services/relevance_assessor.py \
    src/services/risk_assessor.py src/services/scheduler.py \
    src/services/engagement_service.py src/models/engagement_history.py \
    src/orchestrator/jarvis.py
ruff format src/services/notifier.py src/services/relevance_assessor.py \
    src/services/risk_assessor.py src/services/scheduler.py \
    src/services/engagement_service.py src/models/engagement_history.py \
    src/orchestrator/jarvis.py
pytest tests/ -v -k "notifier or engagement or relevance or scheduler or perception"
alembic upgrade head
```
