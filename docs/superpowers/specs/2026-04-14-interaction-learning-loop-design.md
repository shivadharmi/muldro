# Interaction Learning Loop

## Status
Version: v1.0
Date: 2026-04-14
Branch: `improve-surface-design-v1`

## Problem

Jarvis has two execution paths — only one learns:

- **Scheduled perception** (background): Perceiver polls sources -> Librarian extracts entities and memories via `store_memory` tool -> stored in Postgres + Qdrant + Neo4j. This path learns.
- **User message** (interactive): Intent classification -> agent executes -> Presenter formats -> response returned -> **nothing extracted**. This path does not learn.

The `_learn_from_outcome()` method exists at `jarvis.py:473-525` but became orphaned when `_complete_lightweight_run()` was replaced by `_log_interaction()` during the Spec 3A surface refactoring. Even when it was active, it only captured approval decisions and failure context — not general knowledge from agent interactions.

**Result**: When a user asks "Check my active GitHub repos" and the Perceiver returns 39 repos with languages, stars, and activity dates, none of that knowledge is stored. Jarvis cannot recall it in future conversations.

## Goal

Every non-trivial user interaction should produce durable memories — factual knowledge, behavioral preferences, and entity relationships — so Jarvis builds continuity over time. This aligns with:

- **Soul** (Law 5): "Always preserve continuity. Jarvis should remember relevant context and behave as if time exists."
- **Vision** (Pillar 3): "Durable Memory — retain useful context, preferences, relationships, ongoing work."
- **Vision** (Pillar 8): "Personalization with Integrity — become more useful as it learns how the user works."

## Design

### Approach: Dedicated `InteractionLearner` Service

A new focused service that the orchestrator calls asynchronously after each interaction. The service handles intent gating, dedup windowing, source tagging, and delegates to the existing `MemoryService.extract_and_store()` pipeline (which already provides exact match + 0.92 cosine semantic dedup + contradiction detection).

### New File: `src/services/interaction_learner.py`

```
InteractionLearner
  __init__(settings, memory_service, redis)
  learn(user_id, workspace_id, user_message, agent_response, intent, trace_id)
    1. Intent gate — skip SKIP_LEARNING_INTENTS
    2. Dedup window — Redis key "jarvis:learn_cooldown:{user_id}" with 60s TTL
    3. Build source text — "User: {message}\nJarvis: {response}"
    4. Call extract_and_store() with source="interaction" provenance and prompt addendum
    5. Log success/failure at debug level
```

### Intent Gate

Skip learning for intents that produce no meaningful knowledge:

```python
SKIP_LEARNING_INTENTS = {"greeting", "chitchat", "acknowledgment", "simple_question", "memory_operation"}
```

- `greeting`, `chitchat`, `acknowledgment`: Social exchanges with no factual content.
- `simple_question`: Trivial Q&A ("What's 2+2?") — no durable knowledge.
- `memory_operation`: Memory is already being stored/recalled directly via the `store_memory` tool path. Extracting from the response would be redundant or circular.

Learning triggers for: `data_fetch`, `status_query`, `single_read`, `approval_response`, `direct_answer`, plus all complex intents routed through the Planner.

### Dedup Window

Prevents burst scenarios (10 rapid messages about the same topic triggering 10 extraction calls):

```python
key = f"jarvis:learn_cooldown:{user_id}"
if await redis.set(key, "1", ex=60, nx=True):
    # proceed with learning
else:
    # skip — already learning within 60s window
```

Uses Redis `SET NX EX` — atomic, no race conditions, auto-expires.

### Source Text

Combined user input + agent response, giving the extraction prompt both intent context and discovered facts:

```
User: Check my active GitHub repos
Jarvis: You have 39 active repositories on GitHub! Your most recent activity was on the jarvis project (updated April 12)...
```

### Source Tagging

Provenance metadata distinguishes interaction-learned memories from perception-learned ones:

```python
provenance = {"source": "interaction", "intent": intent, "trace_id": trace_id}
```

Passed to `extract_and_store()` and stored in the memory's `provenance` column. Useful for debugging and consolidation.

### Extraction Prompt Addendum

When `source="interaction"`, a short addendum is appended to `MEMORY_EXTRACTION_PROMPT`:

```
When the input is a user-agent dialogue:
- Extract factual knowledge the agent discovered (entities, counts, states, dates)
- Extract user behavioral signals (what topics they care about, what they check on)
- Prefer semantic and preference memories over episodic for recurring patterns
- Do NOT extract the act of asking itself as a memory ("User asked about X" is low value)
```

This nudges extraction toward what was learned rather than what was said:
- "User has 39 GitHub repos" (semantic) — extracted
- "User's most active repo is jarvis (Python, private)" (semantic) — extracted
- "User monitors GitHub repo activity" (preference) — extracted
- "User asked about GitHub repos" (episodic) — suppressed

### Orchestrator Integration

**Initialization** — in `JarvisOrchestrator.__init__()`:

```python
self._interaction_learner: InteractionLearner | None = None
if self._services.memory_service and self._redis:
    self._interaction_learner = InteractionLearner(
        settings=self._settings,
        memory_service=self._services.memory_service,
        redis=self._redis,
    )
```

Graceful degradation — if memory service or Redis unavailable, learning silently skipped.

**`process_message()` wiring** — after Presenter formats response (~line 915):

```python
response_text = result.get("presentation", result.get("presenter", ""))

if self._interaction_learner:
    self._spawn_background(
        self._interaction_learner.learn(
            user_id=user_id,
            workspace_id=workspace_id,
            user_message=message,
            agent_response=response_text,
            intent=intent,
            trace_id=trace.trace_id,
        )
    )
```

**`process_message_stream()` wiring** — same call after `run_completed` event and surface push, before the `"done"` yield (~line 1268). Uses `presenter_text` variable which holds the accumulated Presenter response.

### `extract_and_store()` Change

Add optional `prompt_addendum: str | None` parameter to `MemoryService.extract_and_store()`. When provided, appended to `MEMORY_EXTRACTION_PROMPT` before the Claude call. Keeps the method generic — the interaction hint is passed by the caller.

### Deletions

- `_learn_from_outcome()` (jarvis.py:473-525, ~53 lines) — fully removed. Its approval/failure learning is unnecessary — the Presenter's response text naturally contains approval decisions and failure context, which the general `learn()` method extracts.

## Dedup Layers (Existing)

The interaction learner feeds into the existing multi-layer dedup pipeline:

1. **60s Redis cooldown** (new, in InteractionLearner) — prevents burst extraction
2. **Exact text match** (existing, in extract_and_store) — SQL check for identical `fact_text`
3. **0.92 cosine similarity** (existing, in extract_and_store) — Qdrant vector dedup
4. **Contradiction detection** (existing, deferred via event bus) — Claude-verified supersession
5. **0.95 consolidation** (existing, scheduled job) — periodic near-duplicate merge

No new dedup logic needed — the existing pipeline handles cross-path duplicates (interaction vs. perception learning) through layers 2-5.

## Async Execution

Learning is fully non-blocking:
- Called via `_spawn_background()` — tracked in `_background_tasks` set with done-callback cleanup
- User receives response immediately — learning happens after delivery
- Failures logged at debug level, never surface to the user

Aligns with soul principle: "Jarvis should feel calm, capable" — learning never slows the response.

## Files Touched

| File | Change |
|---|---|
| `src/services/interaction_learner.py` | **New** (~80-100 lines) |
| `src/orchestrator/jarvis.py` | Delete `_learn_from_outcome` (~53 lines), add learner init (~5 lines), add 2 `_spawn_background` calls (~8 lines each) |
| `src/services/memory_service.py` | Add `prompt_addendum` param to `extract_and_store()` (~5 lines) |
| `tests/test_interaction_learner.py` | **New** — unit tests for intent gate, dedup window, extract_and_store delegation |

Total: ~150-200 lines new, ~53 lines deleted, ~10 lines modified.

## Testing

- **Unit**: `InteractionLearner.learn()` — intent gate skips correctly, dedup window works (mock Redis), `extract_and_store()` called with correct source text and provenance (mock memory service)
- **Integration**: Full `process_message()` flow -> verify background learning task is spawned with correct arguments
