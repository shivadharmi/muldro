# Spec 4A: Perception Signal Routing

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 2A (Trust Infrastructure) — relevance feeds into trust-aware delivery
**Builds toward:** Spec 4B (Proactive Insight Surfaces)

## Problem Statement

The perception system detects events but has no interpretation or filtering before action. This spec builds the **backend perception pipeline** — LLM relevance assessment, notification tier routing, Persona batching, cross-source synthesis, and notification rate limiting. No frontend changes.

See parent problem: no interpretation step, no proposal step, wasteful per-message Persona calls.

## Design

### Component 1: LLM Relevance Assessor

New file: `src/services/relevance_assessor.py`

Haiku call that evaluates "should the user care about this right now?"

```python
class RelevanceAssessment(BaseModel):
    relevance_score: float  # 0.0-1.0
    reasoning: str
    relates_to_goals: list[str] = []
    urgency: Literal["immediate", "today", "this_week", "whenever"] = "whenever"
    suggested_actions: list[SuggestedAction] = []
    notification_tier: Literal["push", "briefing", "silent"] = "silent"

class SuggestedAction(BaseModel):
    description: str
    capability: str
    action_input: dict = {}

async def assess_relevance(
    signal: PerceptionSignal,
    user_context: UserContext,
    client: Any,
    model: str = "haiku",
) -> RelevanceAssessment:
    ...
```

**Tier routing logic:**
```
relevance >= 0.7 AND urgency in (immediate, today)  → push
relevance >= 0.4 AND urgency in (today, this_week)  → briefing
relevance >= 0.4 AND urgency == whenever             → briefing
relevance < 0.4                                       → silent
```

### Component 2: Notification Tier Routing

In `jarvis.py` `run_perception_cycle()`, after Librarian extraction, add relevance assessment and route signals:

```python
# After librarian extracts entities from event
assessment = await assess_relevance(signal, user_context, client)

if assessment.notification_tier == "push":
    # Store for Spec 4B to create insight surface
    await self._store_push_signal(signal, assessment, user_id, workspace_id)
    # Also notify via existing notifier (interim until 4B surfaces exist)
    await self._notifier.notify(user_id=user_id, notification_type="insight", ...)

elif assessment.notification_tier == "briefing":
    await memory_service.store_memory(
        memory_type="briefing_item",
        content=f"{signal.summary}\n\nWhy: {assessment.reasoning}",
        metadata={"relevance_score": assessment.relevance_score, "signal_source": signal.source},
        ttl_hours=24,
    )

elif assessment.notification_tier == "silent":
    # Already in world model from Librarian. No notification.
    pass
```

### Component 3: Notification Rate Limiting

Add per-surface rate caps to `notifier.py`:

```python
SURFACE_RATE_LIMITS = {
    "telegram": 5,   # per hour
    "web": 15,
    "slack": 8,
    "email": 3,
}

async def _check_rate_limit(self, user_id: str, surface: str) -> bool:
    key = f"notifier:rate:{user_id}:{surface}"
    count = await self._redis.incr(key)
    if count == 1:
        await self._redis.expire(key, 3600)
    return count <= SURFACE_RATE_LIMITS.get(surface, 10)
```

When rate exceeded, notification held for next briefing instead of dropped.

### Component 4: Notification Priority Score Activation

The existing `compute_priority_score()` (already implemented, output ignored) gets wired into delivery:

```python
# In notifier.py notify() method
score = compute_priority_score(urgency, goal_relevance, novelty, confidence, interruptibility)

if score < 0.3:
    return  # Silent — don't deliver
elif score < 0.6:
    await self._hold_for_briefing(...)  # Batch into briefing
    return
# score >= 0.6: deliver to active surfaces
```

### Component 5: Persona Batching

Remove per-message Persona calls. Replace with periodic batch:

**Remove from `jarvis.py`:** Delete fire-and-forget Persona calls in both `process_message()` and `process_message_stream()`.

**Add to `scheduler.py`:**
```python
async def _tick_persona_batch(self):
    """Run Persona agent on recent interactions every 10th tick (~5 min)."""
    if self._tick_count % 10 != 0:
        return

    # Fetch recent InteractionLogs (or messages) since last batch
    recent = await self._get_recent_interactions(limit=20)
    if len(recent) < 5:
        return  # Not enough data

    summary = "\n".join([f"- {i.message_preview} → {i.intent}" for i in recent])
    await self._call_agent("persona", message=f"Analyze these interactions:\n{summary}", ...)
```

### Component 6: Cross-Source Synthesis Improvement

Remove fixed 30-minute cooldown. Replace with signal-volume trigger:

```python
# In scheduler._tick_perception()
if len(sources_with_events) >= 2 and total_event_count >= 3:
    await self._cross_source_synthesis(sources_with_events, user_id, workspace_id)
```

No arbitrary cooldown — synthesis triggers when there's actual cross-source signal to synthesize.

## Absorbed Issues from Audit

**Issue #18 — Priority score computed but never used:** Wired into delivery decisions (Component 4).

**Issue #5 — No notification rate limiting:** Per-surface rate caps (Component 3).

## Files Changed

### New Files
- `src/services/relevance_assessor.py` — LLM relevance assessment
- `tests/test_relevance_assessor.py`
- `tests/test_notification_rate_limit.py`

### Modified Files
- `src/orchestrator/jarvis.py` — Remove 2 Persona calls (both message paths). Add relevance assessment in `run_perception_cycle()`.
- `src/services/scheduler.py` — Add `_tick_persona_batch()`. Remove 30-min synthesis cooldown. Add signal-volume trigger.
- `src/services/notifier.py` — Add rate limiting. Wire priority score into delivery decisions.
- `src/services/memory_service.py` — `store_briefing_memory()` accepts relevance metadata.
- `src/orchestrator/contracts.py` — Add `notification_tier` to `PerceptionDecision`.

### NOT Modified (saved for Spec 4B)
- No frontend files
- No new surface types
- No engagement history model
- No insight surface components

## Testing Strategy

- Unit tests: relevance assessment parsing, tier routing logic
- Unit tests: rate limiting (allow, block, reset after window)
- Unit tests: priority score → delivery decision mapping
- Unit tests: Persona batch trigger (every 10th tick, minimum 5 interactions)
- Unit tests: cross-source synthesis trigger (2+ sources, 3+ events)
- Integration: perception signal → relevance assessment → correct tier
- Integration: rate limit exceeded → notification held for briefing

## Success Criteria

1. Perception signals assessed for relevance before routing
2. Three notification tiers (push/briefing/silent) route correctly
3. Per-surface rate limiting prevents spam
4. Priority score drives delivery decisions
5. Persona runs batched (every ~5 min), not per-message
6. Cross-source synthesis triggers on signal volume, not fixed cooldown
7. Existing perception pipeline continues working for non-assessed signals

## Blast Radius

**Moderate — perception pipeline + notifier changes.**

| File | Change | Risk |
|------|--------|------|
| `src/orchestrator/jarvis.py` | Remove 2 Persona calls, add relevance step in perception | **HIGH** — core orchestrator |
| `src/services/notifier.py` | Rate limiting + priority filtering | **MEDIUM** — notification delivery |
| `src/services/scheduler.py` | Persona batch, synthesis trigger | **MEDIUM** — background scheduling |
| `src/services/memory_service.py` | Relevance metadata on briefing items | **LOW** — additive parameter |
| `src/orchestrator/contracts.py` | Add field to PerceptionDecision | **LOW** — additive |

### Total: ~15 files (5 modified, 3 new source, 3 new tests, 4 existing tests updated)
