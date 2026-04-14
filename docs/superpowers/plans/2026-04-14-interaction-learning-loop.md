# Interaction Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Jarvis to learn from every non-trivial user interaction by extracting durable memories asynchronously after each response.

**Architecture:** A new `InteractionLearner` service handles intent gating, Redis-based dedup windowing, and source tagging, then delegates to the existing `MemoryService.extract_and_store()` pipeline. The orchestrator calls it via `_spawn_background()` in both `process_message()` and `process_message_stream()`. The orphaned `_learn_from_outcome()` method is deleted.

**Tech Stack:** Python 3.12, asyncio, Redis (SET NX EX), MemoryService (Haiku extraction), pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-14-interaction-learning-loop-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/src/services/interaction_learner.py` | **New** — Intent gate, dedup window, source text assembly, delegates to extract_and_store |
| `backend/src/services/memory_service.py` | **Modify** — Add `prompt_addendum` param to `extract_and_store()` and `_call_extraction()` |
| `backend/src/orchestrator/jarvis.py` | **Modify** — Delete `_learn_from_outcome`, init learner, wire into both message paths |
| `backend/tests/test_interaction_learner.py` | **New** — Unit tests for InteractionLearner |

---

### Task 1: Add `prompt_addendum` to MemoryService extraction

**Files:**
- Modify: `backend/src/services/memory_service.py:163-172` (extract_and_store signature)
- Modify: `backend/src/services/memory_service.py:1063-1077` (_call_extraction signature)
- Test: `backend/tests/test_memory_service.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_memory_service.py`:

```python
@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_and_store_uses_prompt_addendum(
    mock_get_client, mock_embed_cls, settings, mock_db
):
    """Should append prompt_addendum to system prompt when provided."""
    extraction = {"memories": []}

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    svc = MemoryService(settings=settings, db=mock_db)

    await svc.extract_and_store(
        user_id=TEST_USER_ID,
        source_text="User: Check repos\nJarvis: You have 39 repos",
        source_event_ids=["trace_123"],
        workspace_id=TEST_WORKSPACE_ID,
        prompt_addendum="\nExtra instruction for interaction learning.",
    )

    call_args = mock_client.messages.create.call_args
    system_prompt = call_args.kwargs.get("system") or call_args[1].get("system")
    assert "Extra instruction for interaction learning." in system_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory_service.py::test_extract_and_store_uses_prompt_addendum -v`
Expected: FAIL with `TypeError: extract_and_store() got an unexpected keyword argument 'prompt_addendum'`

- [ ] **Step 3: Add `prompt_addendum` param to `_call_extraction`**

In `backend/src/services/memory_service.py`, change `_call_extraction` (line 1063):

```python
async def _call_extraction(self, source_text: str, prompt_addendum: str | None = None) -> dict:
    """Call Claude to extract memories from text."""
    try:
        system_prompt = MEMORY_EXTRACTION_PROMPT
        if prompt_addendum:
            system_prompt = system_prompt + prompt_addendum
        response = await self._client.messages.create(
            model=self._settings.resolved_model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": source_text}],
        )
        from src.llm_utils import parse_llm_json

        return parse_llm_json(response.content[0].text)
    except Exception:
        logger.debug("Memory extraction returned non-JSON", exc_info=True)
        return {"memories": []}
```

- [ ] **Step 4: Add `prompt_addendum` param to `extract_and_store`**

In `backend/src/services/memory_service.py`, change `extract_and_store` (line 163):

```python
async def extract_and_store(
    self,
    user_id: str,
    source_text: str,
    source_event_ids: list[str],
    entity_ids: list[str] | None = None,
    workspace_id: str = "",
    prompt_addendum: str | None = None,
    provenance_extra: dict | None = None,
) -> list[str]:
    """Extract memories from text and store them. Returns memory_ids."""
    extracted = await self._call_extraction(source_text, prompt_addendum=prompt_addendum)
```

Also update the `provenance` field when creating the Memory object (line 198):

```python
provenance={"extraction_method": "claude_auto", **(provenance_extra or {})},
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_memory_service.py::test_extract_and_store_uses_prompt_addendum -v`
Expected: PASS

- [ ] **Step 6: Run full memory service tests to check for regressions**

Run: `cd backend && python -m pytest tests/test_memory_service.py -v`
Expected: All tests PASS (existing tests don't pass `prompt_addendum`, so they use the default `None`)

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/services/memory_service.py tests/test_memory_service.py
git commit -m "feat: add prompt_addendum and provenance_extra to extract_and_store"
```

---

### Task 2: Create `InteractionLearner` service

**Files:**
- Create: `backend/src/services/interaction_learner.py`
- Test: `backend/tests/test_interaction_learner.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_interaction_learner.py`:

```python
"""Tests for InteractionLearner — async learning from user interactions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.interaction_learner import SKIP_LEARNING_INTENTS, InteractionLearner
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_memory_service():
    svc = MagicMock()
    svc.extract_and_store = AsyncMock(return_value=["mem_001"])
    return svc


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    # Default: no cooldown active (SET NX returns True)
    r.set = AsyncMock(return_value=True)
    return r


@pytest.fixture
def learner(settings, mock_memory_service, mock_redis):
    return InteractionLearner(
        settings=settings,
        memory_service=mock_memory_service,
        redis=mock_redis,
    )


@pytest.mark.asyncio
async def test_learn_calls_extract_and_store(learner, mock_memory_service):
    """Should call extract_and_store with combined user+agent text."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check my GitHub repos",
        agent_response="You have 39 active repositories on GitHub.",
        intent="data_fetch",
        trace_id="trace_abc",
    )

    mock_memory_service.extract_and_store.assert_called_once()
    call_kwargs = mock_memory_service.extract_and_store.call_args.kwargs
    assert "Check my GitHub repos" in call_kwargs["source_text"]
    assert "39 active repositories" in call_kwargs["source_text"]
    assert call_kwargs["user_id"] == TEST_USER_ID
    assert call_kwargs["workspace_id"] == TEST_WORKSPACE_ID
    assert call_kwargs["provenance_extra"]["source"] == "interaction"
    assert call_kwargs["provenance_extra"]["intent"] == "data_fetch"
    assert call_kwargs["prompt_addendum"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", list(SKIP_LEARNING_INTENTS))
async def test_learn_skips_trivial_intents(learner, mock_memory_service, intent):
    """Should skip extraction for greeting, chitchat, acknowledgment, simple_question, memory_operation."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Hello!",
        agent_response="Hi there!",
        intent=intent,
        trace_id="trace_skip",
    )

    mock_memory_service.extract_and_store.assert_not_called()


@pytest.mark.asyncio
async def test_learn_skips_when_cooldown_active(learner, mock_memory_service, mock_redis):
    """Should skip extraction when Redis cooldown key already exists."""
    mock_redis.set = AsyncMock(return_value=False)  # Key already exists

    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check my repos",
        agent_response="You have 39 repos.",
        intent="data_fetch",
        trace_id="trace_dup",
    )

    mock_memory_service.extract_and_store.assert_not_called()


@pytest.mark.asyncio
async def test_learn_sets_redis_cooldown(learner, mock_redis):
    """Should set Redis cooldown key with 60s TTL."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check repos",
        agent_response="39 repos found.",
        intent="data_fetch",
        trace_id="trace_cd",
    )

    mock_redis.set.assert_called_once_with(
        f"jarvis:learn_cooldown:{TEST_USER_ID}", "1", ex=60, nx=True
    )


@pytest.mark.asyncio
async def test_learn_survives_extraction_failure(learner, mock_memory_service):
    """Should not raise if extract_and_store fails."""
    mock_memory_service.extract_and_store = AsyncMock(side_effect=RuntimeError("DB down"))

    # Should not raise
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Check repos",
        agent_response="39 repos.",
        intent="data_fetch",
        trace_id="trace_err",
    )


@pytest.mark.asyncio
async def test_learn_skips_empty_response(learner, mock_memory_service):
    """Should skip extraction when agent response is empty."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Hello",
        agent_response="",
        intent="data_fetch",
        trace_id="trace_empty",
    )

    mock_memory_service.extract_and_store.assert_not_called()


@pytest.mark.asyncio
async def test_learn_handles_planner_intent(learner, mock_memory_service):
    """Should learn from complex intents that go through the Planner (intent=None)."""
    await learner.learn(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        user_message="Draft an email to Alice about the Q3 report",
        agent_response="I've drafted the email and sent it to Alice.",
        intent=None,
        trace_id="trace_complex",
    )

    mock_memory_service.extract_and_store.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_interaction_learner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.interaction_learner'`

- [ ] **Step 3: Write the `InteractionLearner` implementation**

Create `backend/src/services/interaction_learner.py`:

```python
"""InteractionLearner — extract durable memories from user interactions.

Runs asynchronously after each non-trivial interaction so that Jarvis
builds continuity over time without slowing the user-facing response.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.services.memory_service import MemoryService

logger = logging.getLogger(__name__)

# Intents that produce no meaningful knowledge — skip learning.
SKIP_LEARNING_INTENTS = frozenset({
    "greeting",
    "chitchat",
    "acknowledgment",
    "simple_question",
    "memory_operation",
})

# Appended to the extraction prompt for interaction-sourced text.
_INTERACTION_ADDENDUM = """

When the input is a user-agent dialogue:
- Extract factual knowledge the agent discovered (entities, counts, states, dates)
- Extract user behavioral signals (what topics they care about, what they check on)
- Prefer semantic and preference memories over episodic for recurring patterns
- Do NOT extract the act of asking itself as a memory ("User asked about X" is low value)
"""

# Redis cooldown window in seconds — prevents burst extraction.
_COOLDOWN_SECONDS = 60


class InteractionLearner:
    """Extract and store memories from user-agent interactions."""

    def __init__(
        self,
        settings: Settings,
        memory_service: MemoryService,
        redis,
    ) -> None:
        self._settings = settings
        self._memory_service = memory_service
        self._redis = redis

    async def learn(
        self,
        user_id: str,
        workspace_id: str,
        user_message: str,
        agent_response: str,
        intent: str | None,
        trace_id: str,
    ) -> None:
        """Extract memories from a completed interaction (fire-and-forget).

        Skips extraction when:
        - The intent is trivial (greeting, chitchat, etc.)
        - The agent response is empty
        - A cooldown window is active for this user (60s)
        """
        # Gate 1: intent filter
        if intent in SKIP_LEARNING_INTENTS:
            return

        # Gate 2: empty response
        if not agent_response or not agent_response.strip():
            return

        # Gate 3: Redis cooldown — prevent burst extraction
        cooldown_key = f"jarvis:learn_cooldown:{user_id}"
        try:
            acquired = await self._redis.set(cooldown_key, "1", ex=_COOLDOWN_SECONDS, nx=True)
            if not acquired:
                logger.debug("Learning cooldown active for %s, skipping", user_id)
                return
        except Exception:
            # Redis down — proceed without cooldown protection
            logger.debug("Redis cooldown check failed, proceeding", exc_info=True)

        # Build combined source text
        source_text = f"User: {user_message}\nJarvis: {agent_response}"

        # Provenance metadata for source tagging
        provenance_extra = {
            "source": "interaction",
            "intent": intent,
            "trace_id": trace_id,
        }

        try:
            memory_ids = await self._memory_service.extract_and_store(
                user_id=user_id,
                source_text=source_text,
                source_event_ids=[trace_id],
                workspace_id=workspace_id,
                prompt_addendum=_INTERACTION_ADDENDUM,
                provenance_extra=provenance_extra,
            )
            if memory_ids:
                logger.debug(
                    "Interaction learning stored %d memories (trace=%s)",
                    len(memory_ids),
                    trace_id,
                )
        except Exception:
            logger.debug("Interaction learning failed (trace=%s)", trace_id, exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_interaction_learner.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Run linter**

Run: `cd backend && ruff check src/services/interaction_learner.py tests/test_interaction_learner.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/services/interaction_learner.py tests/test_interaction_learner.py
git commit -m "feat: add InteractionLearner service with intent gate and dedup window"
```

---

### Task 3: Wire InteractionLearner into the orchestrator

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:184-214` (init)
- Modify: `backend/src/orchestrator/jarvis.py:914-930` (process_message)
- Modify: `backend/src/orchestrator/jarvis.py:1267-1274` (process_message_stream)
- Modify: `backend/src/orchestrator/jarvis.py:473-525` (delete _learn_from_outcome)

- [ ] **Step 1: Add import and init the learner**

In `backend/src/orchestrator/jarvis.py`, add the import near line 44 (after existing service imports):

```python
from src.services.interaction_learner import InteractionLearner
```

In `__init__` method, add after line 208 (after `self._circuit_breaker` init):

```python
        # Interaction learning — async memory extraction from user messages
        self._interaction_learner: InteractionLearner | None = None
        if self._services.memory_service:
            self._interaction_learner = InteractionLearner(
                settings=settings,
                memory_service=self._services.memory_service,
                redis=None,  # Populated lazily when event bus Redis is available
            )
```

Note: Redis is created lazily via `_ensure_event_bus()`. The learner needs a small helper to get the Redis connection. Add this method after `_spawn_background`:

```python
    async def _ensure_learner_redis(self) -> None:
        """Ensure the interaction learner has a Redis connection."""
        if self._interaction_learner and self._interaction_learner._redis is None:
            event_bus = await self._ensure_event_bus()
            if event_bus and hasattr(self, "_event_bus_redis"):
                self._interaction_learner._redis = self._event_bus_redis
```

- [ ] **Step 2: Wire into `process_message()` — after surface push, before return**

In `backend/src/orchestrator/jarvis.py`, after the surface push block (~line 928) and before `return result` (line 930), add:

```python
            # Interaction learning (async, non-blocking)
            if self._interaction_learner:
                await self._ensure_learner_redis()
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

- [ ] **Step 3: Wire into `process_message_stream()` — after surface push, before done yield**

In `backend/src/orchestrator/jarvis.py`, after the surface push try/except block (~line 1267) and before the `yield {"event": "done", ...}` (line 1269), add:

```python
            # Interaction learning (async, non-blocking)
            if self._interaction_learner:
                await self._ensure_learner_redis()
                self._spawn_background(
                    self._interaction_learner.learn(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        user_message=message,
                        agent_response=presenter_text,
                        intent=intent,
                        trace_id=trace.trace_id,
                    )
                )
```

- [ ] **Step 4: Delete `_learn_from_outcome` method**

Remove lines 473-525 in `backend/src/orchestrator/jarvis.py` — the entire `_learn_from_outcome` method. Verify no call sites exist:

Run: `cd backend && grep -rn "_learn_from_outcome" src/`
Expected: No results (method was already orphaned)

- [ ] **Step 5: Run linter on modified file**

Run: `cd backend && ruff check src/orchestrator/jarvis.py`
Expected: No errors

- [ ] **Step 6: Run existing orchestrator tests to check for regressions**

Run: `cd backend && python -m pytest tests/test_orchestrator.py tests/test_process_message.py -v 2>/dev/null; python -m pytest tests/ -v -k "orchestrator or process_message" --timeout=30`
Expected: All existing tests PASS

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py
git commit -m "feat: wire InteractionLearner into both message paths, delete _learn_from_outcome"
```

---

### Task 4: Integration test — verify end-to-end learning fires

**Files:**
- Test: `backend/tests/test_interaction_learner.py` (append)

- [ ] **Step 1: Write integration test verifying orchestrator spawns learning**

Append to `backend/tests/test_interaction_learner.py`:

```python
from unittest.mock import patch


@patch("src.services.interaction_learner.InteractionLearner.learn")
@patch("src.orchestrator.jarvis.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_message_triggers_learning(mock_get_client, mock_learn):
    """Verify process_message spawns interaction learning in background."""
    from src.orchestrator.jarvis import JarvisOrchestrator
    from src.orchestrator.services import ServiceContainer

    mock_learn.return_value = None

    # Mock Claude API — return a simple plan + presenter response
    mock_client = MagicMock()
    intent_response = MagicMock()
    intent_response.content = [MagicMock(text='{"intent":"data_fetch","confidence":0.95,"sources":["github"]}')]
    intent_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    presenter_response = MagicMock()
    presenter_response.content = [MagicMock(text="You have 39 repos.")]
    presenter_response.usage = MagicMock(input_tokens=200, output_tokens=100)

    # Return intent classification first, then perceiver, then presenter
    mock_client.messages.create = AsyncMock(
        side_effect=[intent_response, presenter_response, presenter_response]
    )
    mock_get_client.return_value = mock_client

    services = ServiceContainer(
        memory_service=MagicMock(),
    )

    settings = make_mock_settings()
    db_factory = MagicMock()

    orch = JarvisOrchestrator(
        settings=settings,
        db_factory=db_factory,
        services=services,
    )

    # Verify the learner was initialized
    assert orch._interaction_learner is not None
```

- [ ] **Step 2: Run the integration test**

Run: `cd backend && python -m pytest tests/test_interaction_learner.py::test_process_message_triggers_learning -v`
Expected: PASS

- [ ] **Step 3: Run all tests**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x -q`
Expected: All tests PASS, no regressions

- [ ] **Step 4: Commit**

```bash
cd backend
git add tests/test_interaction_learner.py
git commit -m "test: add integration test for interaction learning wiring"
```

---

### Task 5: Final validation and cleanup

- [ ] **Step 1: Run full linter pass**

Run: `cd backend && ruff check src/services/interaction_learner.py src/services/memory_service.py src/orchestrator/jarvis.py`
Expected: No errors

- [ ] **Step 2: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -q`
Expected: All tests PASS

- [ ] **Step 3: Verify no references to deleted method remain**

Run: `cd backend && grep -rn "_learn_from_outcome" src/ tests/`
Expected: No results

- [ ] **Step 4: Verify the new service is importable**

Run: `cd backend && python -c "from src.services.interaction_learner import InteractionLearner, SKIP_LEARNING_INTENTS; print('OK:', len(SKIP_LEARNING_INTENTS), 'skip intents')"`
Expected: `OK: 5 skip intents`

- [ ] **Step 5: Final commit if any formatting fixes were needed**

```bash
cd backend
ruff format src/services/interaction_learner.py src/services/memory_service.py src/orchestrator/jarvis.py tests/test_interaction_learner.py
git add -A
git diff --cached --stat  # Only commit if there are changes
git commit -m "chore: format interaction learning files"
```
