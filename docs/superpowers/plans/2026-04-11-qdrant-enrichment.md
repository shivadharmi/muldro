# Spec 5A: Qdrant Enrichment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate underutilized Qdrant collections (events, artifacts) and add new ones (conversations, approvals), enrich memory payloads to eliminate Postgres round-trips in TriSearch, add payload indexing, and expire stale memories.

**Architecture:** Each write path (event ingest, conversation summarize, approval decision, artifact create) gets an optional embedding+upsert call after the primary Postgres write. TriSearch's Qdrant result mapper is updated to read richer payloads directly. A new scheduler tick expires memories with TTL. All vector operations are guarded with `if embedding:` and `if vector_store:` for graceful degradation.

**Tech Stack:** Python 3.12, AsyncQdrantClient, Bedrock Titan V2 (1024-dim), SQLAlchemy async, pytest + pytest-asyncio

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/services/vector_store.py` | Modify | Add 2 collection constants, `ensure_indexes()`, create new collections |
| `src/services/event_processor.py` | Modify | Embed events with importance >= 0.3 after Postgres insert |
| `src/orchestrator/jarvis.py` | Modify | Embed conversation summaries after `_summarize_history` |
| `src/api/routes_approvals.py` | Modify | Embed approval decisions on approve/reject |
| `src/api/routes_artifacts.py` | Modify | Embed artifacts on create |
| `src/services/memory_service.py` | Modify | Enrich Qdrant payloads with confidence, stability, entity_ids, preference_strength, scope, created_at |
| `src/services/tri_search.py` | Modify | Use enriched payloads for scoring (skip Postgres round-trip for memories) |
| `src/services/scheduler.py` | Modify | Add `_tick_memory_expiration()` |
| `tests/test_event_embedding.py` | Create | Event embedding tests |
| `tests/test_payload_indexing.py` | Create | Payload indexing tests |
| `tests/test_memory_service.py` | Modify | Test enriched payloads |
| `tests/test_event_processor.py` | Modify | Test event embedding integration |
| `tests/test_tri_search.py` | Modify | Test enriched-payload scoring path |
| `tests/test_scheduler.py` | Modify | Test memory expiration tick |
| `tests/test_approvals_embedding.py` | Create | Approval embedding tests |

---

### Task 1: Add New Collection Constants and `ensure_indexes()` to VectorStore

**Files:**
- Modify: `backend/src/services/vector_store.py`
- Test: `backend/tests/test_payload_indexing.py`

- [ ] **Step 1: Write the failing test for new collection constants**

```python
# tests/test_payload_indexing.py
"""Tests for Qdrant payload indexing and new collection constants."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.vector_store import (
    COLLECTION_APPROVALS,
    COLLECTION_ARTIFACTS,
    COLLECTION_CONVERSATIONS,
    COLLECTION_ENTITIES,
    COLLECTION_EVENTS,
    COLLECTION_MEMORIES,
    VectorStore,
)
from tests.conftest import make_mock_settings


def test_collection_constants_exist():
    """All 6 collection constants should be defined."""
    assert COLLECTION_MEMORIES == "memories"
    assert COLLECTION_ENTITIES == "entities"
    assert COLLECTION_EVENTS == "events"
    assert COLLECTION_ARTIFACTS == "artifacts"
    assert COLLECTION_CONVERSATIONS == "conversations"
    assert COLLECTION_APPROVALS == "approvals"


@pytest.mark.asyncio
async def test_ensure_collections_creates_all_six():
    """ensure_collections should create all 6 collections."""
    settings = make_mock_settings()
    settings.qdrant_url = "http://localhost:6333"
    vs = VectorStore(settings)

    mock_client = AsyncMock()
    mock_client.get_collections.return_value = MagicMock()
    # Simulate collection not found for all
    mock_client.get_collection.side_effect = Exception("not found")
    mock_client.create_collection = AsyncMock()

    vs._client = mock_client
    await vs.ensure_collections()

    assert mock_client.create_collection.call_count == 6
    created_names = [
        call.kwargs.get("collection_name") or call.args[0]
        for call in mock_client.create_collection.call_args_list
    ]
    # Handle keyword-only calls
    created_names = [
        c.kwargs["collection_name"]
        for c in mock_client.create_collection.call_args_list
    ]
    assert set(created_names) == {
        "memories", "entities", "events", "artifacts", "conversations", "approvals"
    }


@pytest.mark.asyncio
async def test_ensure_indexes_creates_payload_indexes():
    """ensure_indexes should create indexes on configured fields."""
    settings = make_mock_settings()
    settings.qdrant_url = "http://localhost:6333"
    vs = VectorStore(settings)

    mock_client = AsyncMock()
    mock_client.get_collections.return_value = MagicMock()
    mock_client.create_payload_index = AsyncMock()
    vs._client = mock_client

    await vs.ensure_indexes()

    # Should have created indexes for memories, entities, events at minimum
    assert mock_client.create_payload_index.call_count >= 5
    index_calls = [
        (c.kwargs["collection_name"], c.kwargs["field_name"])
        for c in mock_client.create_payload_index.call_args_list
    ]
    assert ("memories", "memory_type") in index_calls
    assert ("memories", "confidence") in index_calls
    assert ("entities", "entity_type") in index_calls
    assert ("events", "source") in index_calls
    assert ("events", "importance_score") in index_calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_payload_indexing.py -v`
Expected: FAIL — `COLLECTION_CONVERSATIONS` and `COLLECTION_APPROVALS` not importable, `ensure_indexes` not found.

- [ ] **Step 3: Add constants, update `ensure_collections`, implement `ensure_indexes`**

In `backend/src/services/vector_store.py`, add below existing constants:

```python
COLLECTION_CONVERSATIONS = "conversations"
COLLECTION_APPROVALS = "approvals"
```

Update `ensure_collections` to include all 6:

```python
async def ensure_collections(self) -> None:
    """Create collections if they don't exist."""
    client = await self._get_client()
    if not client:
        return

    from qdrant_client.models import Distance, VectorParams

    collections = (
        COLLECTION_MEMORIES,
        COLLECTION_ENTITIES,
        COLLECTION_EVENTS,
        COLLECTION_ARTIFACTS,
        COLLECTION_CONVERSATIONS,
        COLLECTION_APPROVALS,
    )
    for name in collections:
        try:
            await client.get_collection(name)
        except Exception:
            await client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection: %s", name)
```

Add `ensure_indexes` method to `VectorStore` class:

```python
async def ensure_indexes(self) -> None:
    """Create Qdrant payload indexes for filtered search."""
    client = await self._get_client()
    if not client:
        return
    from qdrant_client.models import PayloadSchemaType

    indexes = {
        COLLECTION_MEMORIES: [
            ("memory_type", PayloadSchemaType.KEYWORD),
            ("confidence", PayloadSchemaType.FLOAT),
        ],
        COLLECTION_ENTITIES: [
            ("entity_type", PayloadSchemaType.KEYWORD),
        ],
        COLLECTION_EVENTS: [
            ("source", PayloadSchemaType.KEYWORD),
            ("event_type", PayloadSchemaType.KEYWORD),
            ("importance_score", PayloadSchemaType.FLOAT),
        ],
    }
    for collection, fields in indexes.items():
        for field_name, schema_type in fields:
            try:
                await client.create_payload_index(
                    collection_name=collection,
                    field_name=field_name,
                    field_schema=schema_type,
                )
            except Exception:
                pass  # index may already exist
```

Also update `hybrid_search` default collections to include conversations and approvals:

```python
if not collections:
    collections = [
        COLLECTION_MEMORIES, COLLECTION_ENTITIES, COLLECTION_EVENTS,
        COLLECTION_CONVERSATIONS, COLLECTION_APPROVALS,
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_payload_indexing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/services/vector_store.py tests/test_payload_indexing.py
git commit -m "feat(spec5a): add conversations/approvals collections, payload indexing"
```

---

### Task 2: Populate Events Collection on Ingest

**Files:**
- Modify: `backend/src/services/event_processor.py`
- Create: `backend/tests/test_event_embedding.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event_embedding.py
"""Tests for event embedding into Qdrant after ingest."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_processor import EventProcessor
from tests.conftest import TEST_USER_ID, make_mock_settings, make_raw_event


def _make_claude_response(scores: dict) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(scores))]
    return response


HIGH_SCORES = {
    "importance_score": 0.85,
    "urgency_score": 0.7,
    "confidence_score": 0.9,
    "importance_signals": {
        "from_priority_person": True,
        "contains_deadline": False,
        "contains_question": True,
        "related_to_active_project": True,
    },
    "summary": "Important event",
}

LOW_SCORES = {**HIGH_SCORES, "importance_score": 0.2}


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_above_threshold(mock_get_client):
    """Events with importance >= 0.3 should be embedded into Qdrant."""
    settings = make_mock_settings()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_claude_response(HIGH_SCORES)
    )
    mock_get_client.return_value = mock_client

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=[0.1] * 1024)

    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )
    event_id = await processor.process(make_raw_event(), TEST_USER_ID)

    assert event_id is not None
    mock_vector_store.upsert.assert_called_once()
    call_kwargs = mock_vector_store.upsert.call_args
    assert call_kwargs.kwargs["collection"] == "events"
    assert call_kwargs.kwargs["user_id"] == TEST_USER_ID
    payload = call_kwargs.kwargs["payload"]
    assert "source" in payload
    assert "importance_score" in payload


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_below_threshold_skipped(mock_get_client):
    """Events with importance < 0.3 should NOT be embedded."""
    settings = make_mock_settings()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_claude_response(LOW_SCORES)
    )
    mock_get_client.return_value = mock_client

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()

    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )
    event_id = await processor.process(make_raw_event(), TEST_USER_ID)

    assert event_id is not None
    mock_vector_store.upsert.assert_not_called()


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_graceful_on_embed_failure(mock_get_client):
    """If embedding returns None, Qdrant upsert should be skipped (no crash)."""
    settings = make_mock_settings()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_claude_response(HIGH_SCORES)
    )
    mock_get_client.return_value = mock_client

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=None)

    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )
    event_id = await processor.process(make_raw_event(), TEST_USER_ID)

    assert event_id is not None
    mock_vector_store.upsert.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_event_embedding.py -v`
Expected: FAIL — `EventProcessor.__init__` doesn't accept `embedding_service` or `vector_store`.

- [ ] **Step 3: Implement event embedding in EventProcessor**

In `backend/src/services/event_processor.py`:

1. Add `embedding_service` and `vector_store` to `__init__` (add to TYPE_CHECKING imports):

```python
if TYPE_CHECKING:
    from src.services.dead_letter import DeadLetterService
    from src.services.embedding_service import EmbeddingService
    from src.services.event_bus import EventBus
    from src.services.memory_service import MemoryService
    from src.services.notifier import Notifier
    from src.services.vector_store import VectorStore
    from src.services.world_model import WorldModel
```

Update `__init__` signature — add parameters after `notifier`:

```python
def __init__(
    self,
    settings: Settings,
    db: AsyncSession,
    world_model: WorldModel | None = None,
    memory_service: MemoryService | None = None,
    dead_letter: DeadLetterService | None = None,
    event_bus: EventBus | None = None,
    notifier: Notifier | None = None,
    embedding_service: EmbeddingService | None = None,
    vector_store: VectorStore | None = None,
):
    # ... existing assignments ...
    self._embedding_service = embedding_service
    self._vector_store = vector_store
```

2. Add embedding call in `_process_inner`, after `await self._db.commit()` and the metrics recording block, before the event bus publish:

```python
# Embed into Qdrant for vector search (importance >= 0.3 only)
if (
    (event.importance_score or 0) >= 0.3
    and self._embedding_service
    and self._vector_store
):
    try:
        text = f"{event.title or ''}: {event.summary or ''}"
        embedding = await self._embedding_service.embed_text(text)
        if embedding:
            await self._vector_store.upsert(
                collection="events",
                id=event.event_id,
                vector=embedding,
                payload={
                    "event_type": event.event_type,
                    "source": event.source,
                    "importance_score": event.importance_score,
                    "occurred_at": event.occurred_at.isoformat()
                    if event.occurred_at
                    else None,
                    "actor": (event.actor_entities[0] or {}).get("name")
                    if event.actor_entities
                    else None,
                },
                user_id=event.user_id,
            )
    except Exception:
        logger.debug("Event embedding failed for %s", event_id, exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_event_embedding.py -v`
Expected: PASS

- [ ] **Step 5: Run existing event processor tests to verify no regression**

Run: `cd backend && python -m pytest tests/test_event_processor.py -v`
Expected: PASS (existing tests don't pass embedding_service/vector_store, so those remain None).

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/services/event_processor.py tests/test_event_embedding.py
git commit -m "feat(spec5a): embed events into Qdrant on ingest (importance >= 0.3)"
```

---

### Task 3: Enrich Memory Payloads

**Files:**
- Modify: `backend/src/services/memory_service.py`
- Modify: `backend/tests/test_memory_service.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_memory_service.py`:

```python
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_memory_upsert_includes_enriched_payload(mock_get_client):
    """Memory Qdrant payloads should include confidence, stability, entity_ids, scope."""
    settings = make_mock_settings()
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_vector_store = AsyncMock()

    svc = MemoryService(
        settings=settings,
        db=mock_db,
        vector_store=mock_vector_store,
    )
    # Patch the embedder to return a fake embedding
    svc._embedder = AsyncMock()
    svc._embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)

    memory_id = await svc.store_memory(
        user_id="usr_test",
        fact_text="Test fact",
        memory_type="semantic",
        scope="general",
        entity_ids=["ent_abc"],
        workspace_id="ws_test",
    )

    mock_vector_store.upsert.assert_called_once()
    payload = mock_vector_store.upsert.call_args.kwargs.get("payload") or mock_vector_store.upsert.call_args[0][3]

    # Enriched fields
    assert payload["memory_type"] == "semantic"
    assert payload["fact_text"] == "Test fact"
    assert payload["confidence"] == 0.8
    assert payload["stability_score"] == 0.0
    assert payload["entity_ids"] == ["ent_abc"]
    assert payload["scope"] == "general"
    assert "created_at" in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory_service.py::test_memory_upsert_includes_enriched_payload -v`
Expected: FAIL — current payload only has `memory_type`, `fact_text`, `user_id`.

- [ ] **Step 3: Create `_build_memory_payload` helper and use it in all upsert sites**

In `backend/src/services/memory_service.py`, add a helper method to the `MemoryService` class:

```python
@staticmethod
def _build_memory_payload(
    memory_type: str,
    fact_text: str,
    user_id: str,
    confidence: float = 0.5,
    stability_score: float = 0.0,
    entity_ids: list[str] | None = None,
    scope: str | None = None,
    preference_strength: str | None = None,
) -> dict:
    """Build enriched Qdrant payload for a memory."""
    from datetime import datetime, timezone

    return {
        "memory_type": memory_type,
        "fact_text": fact_text,
        "user_id": user_id,
        "confidence": confidence,
        "stability_score": stability_score,
        "entity_ids": entity_ids or [],
        "scope": scope or "general",
        "preference_strength": preference_strength,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
```

Then replace every Qdrant upsert payload in the file. There are 6 upsert sites. For each, replace the inline `{"memory_type": ..., "fact_text": ..., "user_id": ...}` dict with a call to `self._build_memory_payload(...)`.

Example — in `extract_and_store`:
```python
if self._vector_store and embedding:
    await self._vector_store.upsert(
        "memories",
        memory_id,
        embedding,
        self._build_memory_payload(
            memory_type=mem_data.get("memory_type", "semantic"),
            fact_text=fact_text,
            user_id=user_id,
            confidence=mem_data.get("confidence", 0.5),
            stability_score=0.0,
            entity_ids=entity_ids,
            scope=mem_data.get("scope", "general"),
        ),
        user_id,
    )
```

In `extract_preferences`:
```python
if self._vector_store and embedding:
    await self._vector_store.upsert(
        "memories",
        memory_id,
        embedding,
        self._build_memory_payload(
            memory_type="preference",
            fact_text=fact_text,
            user_id=user_id,
            confidence=pref_data.get("confidence", 0.5),
            scope=pref_data.get("category", "general"),
            preference_strength=pref_data.get("strength", "moderate"),
        ),
        user_id,
    )
```

In `store_goal_memory`:
```python
if self._vector_store and embedding:
    await self._vector_store.upsert(
        "memories",
        memory_id,
        embedding,
        self._build_memory_payload(
            memory_type="goal",
            fact_text=fact_text,
            user_id=user_id,
            confidence=0.9,
            stability_score=0.5,
            entity_ids=entity_ids,
            scope="planning",
        ),
        user_id,
    )
```

In `store_instruction_memory`:
```python
if self._vector_store and embedding:
    await self._vector_store.upsert(
        "memories",
        memory_id,
        embedding,
        self._build_memory_payload(
            memory_type="preference",
            fact_text=fact_text,
            user_id=user_id,
            confidence=0.95,
            stability_score=0.8,
            scope="general",
        ),
        user_id,
    )
```

In `store_briefing_memory`:
```python
if self._vector_store and embedding:
    await self._vector_store.upsert(
        "memories",
        memory_id,
        embedding,
        self._build_memory_payload(
            memory_type="briefing_item",
            fact_text=text,
            user_id=user_id,
            confidence=0.8,
            stability_score=0.3,
            scope="planning",
        ),
        user_id,
    )
```

In `store_memory`:
```python
if self._vector_store and embedding:
    await self._vector_store.upsert(
        "memories",
        memory_id,
        embedding,
        self._build_memory_payload(
            memory_type=memory_type,
            fact_text=fact_text,
            user_id=user_id,
            confidence=0.8,
            entity_ids=entity_ids,
            scope=scope,
        ),
        user_id,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_memory_service.py::test_memory_upsert_includes_enriched_payload -v`
Expected: PASS

- [ ] **Step 5: Run all memory service tests for regression**

Run: `cd backend && python -m pytest tests/test_memory_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/services/memory_service.py tests/test_memory_service.py
git commit -m "feat(spec5a): enrich memory Qdrant payloads with confidence, stability, entity_ids"
```

---

### Task 4: TriSearch Uses Enriched Payloads (Skip Postgres Round-Trip)

**Files:**
- Modify: `backend/src/services/tri_search.py`
- Modify: `backend/tests/test_tri_search.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_tri_search.py` (or create if it doesn't exist):

```python
@pytest.mark.asyncio
async def test_qdrant_memory_results_use_enriched_payload():
    """Qdrant memory results should use confidence/stability from payload, not defaults."""
    from src.services.tri_search import TriSearchService, _compute_final_score

    settings = make_mock_settings()

    mock_vector_store = AsyncMock()
    mock_vector_store.hybrid_search = AsyncMock(return_value=[
        {
            "id": "mem_001",
            "score": 0.92,
            "collection": "memories",
            "payload": {
                "_original_id": "mem_001",
                "fact_text": "User prefers concise briefings",
                "memory_type": "preference",
                "confidence": 0.95,
                "stability_score": 0.8,
                "entity_ids": ["ent_abc"],
                "scope": "communication",
                "preference_strength": "strong",
                "created_at": "2026-04-10T10:00:00+00:00",
            },
        }
    ])

    mock_embedder = AsyncMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)

    svc = TriSearchService(
        settings=settings,
        vector_store=mock_vector_store,
        embedder=mock_embedder,
    )

    mock_db = AsyncMock()
    results = await svc.search(
        query="briefing preferences",
        user_id="usr_test",
        workspace_id="ws_test",
        db=mock_db,
        limit=10,
    )

    assert len(results) >= 1
    result = results[0]
    # Should use payload confidence (0.95), not default (0.5)
    assert result["confidence"] == 0.95
    # Should use payload stability (0.8), not default (0.5)
    assert result["stability"] == 0.8
    # preference_strength should be included
    assert result.get("preference_strength") == "strong"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tri_search.py::test_qdrant_memory_results_use_enriched_payload -v`
Expected: FAIL — `preference_strength` not in result, or the test file doesn't exist yet.

- [ ] **Step 3: Update `_search_qdrant` to read enriched payload fields**

In `backend/src/services/tri_search.py`, update `_search_qdrant` method. Replace the result-building loop:

```python
results: list[dict] = []
for hit in raw:
    payload = hit.get("payload") or {}
    collection = hit.get("collection", "")
    result_type = _collection_to_type(collection)
    results.append(
        {
            "id": hit.get("id", ""),
            "title": (
                payload.get("title")
                or payload.get("fact_text", "")[:80]
                or payload.get("canonical_name", "")
            ),
            "text": (
                payload.get("text")
                or payload.get("fact_text", "")
                or payload.get("summary", "")
            ),
            "score": hit.get("score", 0.0),
            "source_db": "qdrant",
            "result_type": result_type,
            "confidence": payload.get("confidence", 0.5),
            "stability": payload.get("stability_score", 0.5),
            "timestamp": payload.get(
                "created_at",
                payload.get("occurred_at"),
            ),
            "entity_ids": payload.get("entity_ids"),
            "preference_strength": payload.get("preference_strength"),
        }
    )
return results
```

Also update `_compute_final_score` to add a preference strength boost:

```python
def _compute_final_score(result: dict) -> float:
    """Weighted composite of rerank, recency, confidence, etc."""
    rerank = result.get("rerank_score", result.get("score", 0.0))
    recency = _compute_recency(result)
    confidence = result.get("confidence", 0.5)
    stability = result.get("stability", 0.5)
    entity_overlap = result.get("entity_overlap", 0.0)
    score = (
        _W_RERANK * rerank
        + _W_RECENCY * recency
        + _W_CONFIDENCE * confidence
        + _W_STABILITY * stability
        + _W_ENTITY_OVERLAP * entity_overlap
    )
    # Boost strong preferences
    strength = result.get("preference_strength")
    if strength == "strong":
        score += 0.05
    elif strength == "weak":
        score -= 0.03
    return score
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_tri_search.py::test_qdrant_memory_results_use_enriched_payload -v`
Expected: PASS

- [ ] **Step 5: Run all tri_search tests**

Run: `cd backend && python -m pytest tests/test_tri_search.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/services/tri_search.py tests/test_tri_search.py
git commit -m "feat(spec5a): TriSearch reads enriched payloads, skips Postgres round-trip"
```

---

### Task 5: Embed Conversation Summaries

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py`

- [ ] **Step 1: Write the failing test**

Add to an appropriate test file (e.g., `backend/tests/test_jarvis_conversation_embedding.py`):

```python
# tests/test_jarvis_conversation_embedding.py
"""Test that conversation summaries are embedded into Qdrant."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_summarize_history_embeds_to_qdrant():
    """After summarizing history, the summary should be upserted to Qdrant."""
    from tests.conftest import make_mock_settings

    settings = make_mock_settings()
    settings.use_bedrock = False

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=[0.1] * 1024)

    with patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client:
        mock_client = MagicMock()
        # _summarize_history calls Claude
        summary_response = MagicMock()
        summary_response.content = [MagicMock(type="text", text="Summary of conversation.")]
        mock_client.messages.create = AsyncMock(return_value=summary_response)
        mock_get_client.return_value = mock_client

        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator(settings=settings)
        orch._vector_store = mock_vector_store
        orch._embedding_service = mock_embedding_service

        lines = ["User: Hello", "Assistant: Hi there", "User: What's up?"]
        summary = await orch._summarize_history(lines, conversation_id="conv_test123")

        assert summary  # got a summary
        mock_vector_store.upsert.assert_called_once()
        call_kwargs = mock_vector_store.upsert.call_args.kwargs
        assert call_kwargs["collection"] == "conversations"
        assert call_kwargs["id"] == "conv_test123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_jarvis_conversation_embedding.py -v`
Expected: FAIL — `_summarize_history` doesn't accept `conversation_id` and doesn't embed.

- [ ] **Step 3: Implement conversation embedding**

In `backend/src/orchestrator/jarvis.py`, update `_summarize_history` to accept an optional `conversation_id` and embed after summarization:

```python
async def _summarize_history(
    self, lines: list[str], conversation_id: str | None = None
) -> str:
    """Summarize older conversation messages using Haiku (cheap, fast)."""
    try:
        if self._settings.use_bedrock:
            model = BEDROCK_MODEL_TIERS["haiku"]
        else:
            model = MODEL_TIERS["haiku"]

        text = "\n".join(lines)[:4000]
        response = await self._client.messages.create(
            model=model,
            max_tokens=300,
            temperature=0,
            system=[
                {
                    "type": "text",
                    "text": (
                        "Summarize this conversation in 2-3 sentences. "
                        "Focus on: topics discussed, decisions made, "
                        "and any pending items."
                    ),
                }
            ],
            messages=[{"role": "user", "content": text}],
        )
        summary = "".join(b.text for b in response.content if b.type == "text")

        # Embed conversation summary into Qdrant
        if (
            conversation_id
            and summary
            and getattr(self, "_vector_store", None)
            and getattr(self, "_embedding_service", None)
        ):
            try:
                embedding = await self._embedding_service.embed_text(summary)
                if embedding:
                    await self._vector_store.upsert(
                        collection="conversations",
                        id=conversation_id,
                        vector=embedding,
                        payload={
                            "conversation_id": conversation_id,
                            "message_count": len(lines),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        },
                        user_id=self._current_user_id or "",
                    )
            except Exception:
                logger.debug(
                    "Conversation embedding failed for %s",
                    conversation_id,
                    exc_info=True,
                )

        return summary
    except Exception:
        logger.debug("History summarization failed", exc_info=True)
        return "\n".join(lines)[:500] + "..."
```

Also update the call site in `_load_conversation_history` (line ~2157) to pass `conversation_id`:

```python
summary = await self._summarize_history(older, conversation_id=conversation_id)
```

Note: `_load_conversation_history` already receives `conversation_id` as its first parameter.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_jarvis_conversation_embedding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py tests/test_jarvis_conversation_embedding.py
git commit -m "feat(spec5a): embed conversation summaries into Qdrant"
```

---

### Task 6: Embed Approval Decisions

**Files:**
- Modify: `backend/src/api/routes_approvals.py`
- Create: `backend/tests/test_approvals_embedding.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_approvals_embedding.py
"""Tests for approval decision embedding into Qdrant."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


@pytest.mark.asyncio
async def test_approve_embeds_to_qdrant():
    """Approving an action should embed the decision into Qdrant."""
    from src.api.routes_approvals import _embed_approval_decision

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=[0.1] * 1024)

    await _embed_approval_decision(
        approval_id="apr_test123",
        approval_type="email.send",
        summary="Send email to investor",
        risk_level="medium",
        outcome="approved",
        user_id=TEST_USER_ID,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )

    mock_embedding_service.embed_text.assert_called_once()
    mock_vector_store.upsert.assert_called_once()
    call_kwargs = mock_vector_store.upsert.call_args.kwargs
    assert call_kwargs["collection"] == "approvals"
    assert call_kwargs["id"] == "apr_test123"
    payload = call_kwargs["payload"]
    assert payload["outcome"] == "approved"
    assert payload["capability"] == "email.send"
    assert payload["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_embed_approval_graceful_on_failure():
    """Embedding failure should not crash the approval flow."""
    from src.api.routes_approvals import _embed_approval_decision

    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=None)
    mock_vector_store = AsyncMock()

    # Should not raise
    await _embed_approval_decision(
        approval_id="apr_test",
        approval_type="email.send",
        summary="Test",
        risk_level="low",
        outcome="rejected",
        user_id=TEST_USER_ID,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )

    mock_vector_store.upsert.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_approvals_embedding.py -v`
Expected: FAIL — `_embed_approval_decision` doesn't exist.

- [ ] **Step 3: Implement `_embed_approval_decision` helper and wire into approve/reject**

In `backend/src/api/routes_approvals.py`, add the helper function near the bottom (before `_get_approval`):

```python
async def _embed_approval_decision(
    approval_id: str,
    approval_type: str,
    summary: str,
    risk_level: str,
    outcome: str,
    user_id: str,
    embedding_service,
    vector_store,
) -> None:
    """Embed approval decision into Qdrant (best-effort)."""
    try:
        text = f"{approval_type}: {summary} → {outcome}"
        embedding = await embedding_service.embed_text(text)
        if embedding:
            await vector_store.upsert(
                collection="approvals",
                id=approval_id,
                vector=embedding,
                payload={
                    "capability": approval_type,
                    "risk_level": risk_level,
                    "outcome": outcome,
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                },
                user_id=user_id,
            )
    except Exception:
        logger.debug(
            "Approval embedding failed for %s", approval_id, exc_info=True
        )
```

Then wire into `approve_action` — after `await db.commit()` (line 179) and before the Redis publish block:

```python
# Embed approval decision into Qdrant
try:
    from src.services.embedding_service import EmbeddingService
    from src.services.vector_store import VectorStore

    if settings.qdrant_url:
        vs = VectorStore(settings)
        es = EmbeddingService(settings)
        await _embed_approval_decision(
            approval_id=approval_id,
            approval_type=approval.approval_type or "",
            summary=approval.summary or "",
            risk_level=approval.risk_level or "low",
            outcome="approved",
            user_id=user_id,
            embedding_service=es,
            vector_store=vs,
        )
except Exception:
    logger.debug("Approval embedding failed", exc_info=True)
```

Wire the same into `reject_action` — after `await db.commit()` (line 391):

```python
# Embed rejection decision into Qdrant
try:
    from src.services.embedding_service import EmbeddingService
    from src.services.vector_store import VectorStore

    if settings.qdrant_url:
        vs = VectorStore(settings)
        es = EmbeddingService(settings)
        await _embed_approval_decision(
            approval_id=approval_id,
            approval_type=approval.approval_type or "",
            summary=approval.summary or "",
            risk_level=approval.risk_level or "low",
            outcome="rejected",
            user_id=user_id,
            embedding_service=es,
            vector_store=vs,
        )
except Exception:
    logger.debug("Rejection embedding failed", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_approvals_embedding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/api/routes_approvals.py tests/test_approvals_embedding.py
git commit -m "feat(spec5a): embed approval decisions into Qdrant"
```

---

### Task 7: Embed Artifacts on Create

**Files:**
- Modify: `backend/src/api/routes_artifacts.py`

- [ ] **Step 1: Write the failing test**

Add to existing artifact tests or create `backend/tests/test_artifact_embedding.py`:

```python
# tests/test_artifact_embedding.py
"""Test artifact embedding into Qdrant on create."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.mark.asyncio
async def test_embed_artifact_on_create():
    """Creating an artifact should embed title+description into Qdrant."""
    from src.api.routes_artifacts import _embed_artifact

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=[0.1] * 1024)

    await _embed_artifact(
        artifact_id="art_test123",
        title="Q1 Report",
        description="Quarterly financial summary",
        artifact_type="document",
        mime_type="application/pdf",
        user_id=TEST_USER_ID,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )

    mock_vector_store.upsert.assert_called_once()
    call_kwargs = mock_vector_store.upsert.call_args.kwargs
    assert call_kwargs["collection"] == "artifacts"
    assert call_kwargs["id"] == "art_test123"
    payload = call_kwargs["payload"]
    assert payload["artifact_type"] == "document"
    assert payload["mime_type"] == "application/pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_artifact_embedding.py -v`
Expected: FAIL — `_embed_artifact` doesn't exist.

- [ ] **Step 3: Implement `_embed_artifact` helper and wire into create_artifact**

In `backend/src/api/routes_artifacts.py`, add the helper:

```python
async def _embed_artifact(
    artifact_id: str,
    title: str | None,
    description: str | None,
    artifact_type: str,
    mime_type: str | None,
    user_id: str,
    embedding_service,
    vector_store,
) -> None:
    """Embed artifact metadata into Qdrant (best-effort)."""
    try:
        text = f"{title or ''}: {description or ''}"
        if not text.strip().strip(":").strip():
            return
        embedding = await embedding_service.embed_text(text)
        if embedding:
            from datetime import datetime, timezone

            await vector_store.upsert(
                collection="artifacts",
                id=artifact_id,
                vector=embedding,
                payload={
                    "artifact_type": artifact_type,
                    "mime_type": mime_type or "",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                user_id=user_id,
            )
    except Exception:
        import logging

        logging.getLogger(__name__).debug(
            "Artifact embedding failed for %s", artifact_id, exc_info=True
        )
```

Wire into `create_artifact` — after `await db.commit()`:

```python
# Embed artifact into Qdrant for vector search
try:
    from src.services.embedding_service import EmbeddingService
    from src.services.vector_store import VectorStore

    if settings.qdrant_url:
        vs = VectorStore(settings)
        es = EmbeddingService(settings)
        await _embed_artifact(
            artifact_id=artifact_id,
            title=req.title,
            description=(req.metadata_ or {}).get("description"),
            artifact_type=req.artifact_type,
            mime_type=req.mime_type,
            user_id=user_id,
            embedding_service=es,
            vector_store=vs,
        )
except Exception:
    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_artifact_embedding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/api/routes_artifacts.py tests/test_artifact_embedding.py
git commit -m "feat(spec5a): embed artifacts into Qdrant on create"
```

---

### Task 8: Memory Expiration Scheduler Job

**Files:**
- Modify: `backend/src/services/scheduler.py`
- Modify: `backend/tests/test_scheduler.py` (or create `backend/tests/test_memory_expiration.py`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_expiration.py
"""Tests for memory expiration scheduler tick."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


@pytest.mark.asyncio
async def test_tick_memory_expiration_marks_expired():
    """Expired memories should be marked 'expired' and deleted from Qdrant."""
    from src.services.scheduler import SchedulerLoop

    settings = make_mock_settings()
    scheduler = SchedulerLoop(settings=settings)

    # Create a fake expired memory
    fake_mem = MagicMock()
    fake_mem.memory_id = "mem_expired1"
    fake_mem.status = "active"
    fake_mem.ttl_days = 7
    fake_mem.created_at = datetime.now(timezone.utc) - timedelta(days=10)

    mock_result = MagicMock()
    mock_result.scalars.return_value = [fake_mem]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_vector_store = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    await scheduler._tick_memory_expiration(mock_factory, mock_vector_store)

    assert fake_mem.status == "expired"
    mock_vector_store.delete.assert_called_once_with("memories", "mem_expired1")
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_tick_memory_expiration_no_expired():
    """When no memories are expired, no Qdrant deletes should happen."""
    from src.services.scheduler import SchedulerLoop

    settings = make_mock_settings()
    scheduler = SchedulerLoop(settings=settings)

    mock_result = MagicMock()
    mock_result.scalars.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_vector_store = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    await scheduler._tick_memory_expiration(mock_factory, mock_vector_store)

    mock_vector_store.delete.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory_expiration.py -v`
Expected: FAIL — `_tick_memory_expiration` doesn't exist.

- [ ] **Step 3: Implement `_tick_memory_expiration` in SchedulerLoop**

In `backend/src/services/scheduler.py`, add the method to `SchedulerLoop`:

```python
async def _tick_memory_expiration(self, factory, vector_store=None) -> None:
    """Mark expired memories and cascade delete from Qdrant."""
    try:
        from sqlalchemy import func, select

        from src.models.memory import Memory

        async with factory() as db:
            result = await db.execute(
                select(Memory)
                .where(
                    Memory.status == "active",
                    Memory.ttl_days.isnot(None),
                    Memory.created_at
                    + func.make_interval(days=Memory.ttl_days)
                    < func.now(),
                )
                .limit(100)
            )
            expired = list(result.scalars())

            if not expired:
                return

            for mem in expired:
                mem.status = "expired"
                if vector_store:
                    try:
                        await vector_store.delete("memories", mem.memory_id)
                    except Exception:
                        logger.debug(
                            "Qdrant delete failed for %s",
                            mem.memory_id,
                            exc_info=True,
                        )

            await db.commit()
            logger.info("Memory expiration: %d memories expired", len(expired))
    except Exception:
        logger.warning("Memory expiration tick error", exc_info=True)
```

Wire into `_tick` — add after the eviction block (step 4a), in the same `if self._tick_count % 5 == 0` guard:

```python
# 4a. Eviction + DLQ retry + Memory expiration — every 5th tick (~150s)
self._tick_count = getattr(self, "_tick_count", 0) + 1
if self._tick_count % 5 == 0:
    await self._tick_eviction(factory)
    await self._tick_dlq_retry(factory)
    # Memory expiration — cascade to Qdrant
    vector_store = None
    if self._settings.qdrant_url:
        from src.services.vector_store import VectorStore

        vector_store = VectorStore(self._settings)
    await self._tick_memory_expiration(factory, vector_store)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_memory_expiration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/services/scheduler.py tests/test_memory_expiration.py
git commit -m "feat(spec5a): add memory expiration scheduler job with Qdrant cascade"
```

---

### Task 9: Integration — Wire `ensure_indexes()` into Startup

**Files:**
- Modify: `backend/src/services/vector_store.py` (already done)
- Modify: Startup code that calls `ensure_collections()`

- [ ] **Step 1: Find where `ensure_collections` is called at startup**

Run: `cd backend && grep -rn "ensure_collections" src/`

- [ ] **Step 2: Add `ensure_indexes()` call after `ensure_collections()`**

Wherever `ensure_collections()` is called (likely in app startup or service container init), add:

```python
await vector_store.ensure_collections()
await vector_store.ensure_indexes()
```

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x -q`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd backend && git add -A
git commit -m "feat(spec5a): wire ensure_indexes into startup"
```

---

### Task 10: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60`
Expected: All tests PASS

- [ ] **Step 2: Run ruff linting**

Run: `cd backend && ruff check src/ tests/ && ruff format --check src/ tests/`
Expected: No errors

- [ ] **Step 3: Fix any lint issues**

Run: `cd backend && ruff check src/ tests/ --fix && ruff format src/ tests/`

- [ ] **Step 4: Final commit if any formatting changes**

```bash
cd backend && git add -A
git commit -m "chore(spec5a): lint and format"
```

---

## Summary of Spec Coverage

| Spec Component | Task(s) | Status |
|---------------|---------|--------|
| 1. Populate events collection | Task 2 | |
| 2. Add conversations collection | Task 1 + Task 5 | |
| 3. Add approvals collection | Task 1 + Task 6 | |
| 4. Populate artifacts collection | Task 7 | |
| 5. Payload indexing | Task 1 + Task 9 | |
| 6. Richer memory payloads | Task 3 + Task 4 | |
| 7. Memory expiration job | Task 8 | |
| Issue #3 (expired memories in search) | Task 8 | |
| Issue #25 (preference strength unused) | Task 3 + Task 4 | |
