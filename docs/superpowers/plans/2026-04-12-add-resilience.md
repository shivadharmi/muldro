# Add Resilience — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the intelligence pipeline robust against transient failures — no permanently lost embeddings, fast-fail on Neo4j outage, and stale Qdrant data refreshed automatically.

**Architecture:** Add a `dead_letter` parameter to `MemoryService` and replace silent embedding skips with DLQ enqueue. Add a lightweight `_Neo4jCircuit` breaker to `GraphEngine`. Add `set_payload` to `VectorStore` for payload-only updates. Extend `ensure_indexes` to all 6 collections. Add scheduler ticks for DLQ retry of `failed_embedding` entries and daily stability refresh.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (auto mode), SQLAlchemy async, Qdrant, Neo4j AsyncGraphDatabase

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/services/memory_service.py` | Modify | Add dead_letter param, _enqueue_failed_embedding, replace 6 silent skips |
| `src/services/world_model.py` | Modify | DLQ enqueue on entity embedding failure |
| `src/services/graph_engine.py` | Modify | Add _Neo4jCircuit, integrate into all write/read methods |
| `src/services/vector_store.py` | Modify | Add set_payload(), extend ensure_indexes() |
| `src/services/scheduler.py` | Modify | Add _retry_failed_embedding, _tick_stability_refresh |
| `src/services/worker.py` | Modify | Pass dead_letter to MemoryService |
| `tests/test_embedding_dlq.py` | Create | DLQ enqueue + retry tests |
| `tests/test_neo4j_circuit_breaker.py` | Create | Circuit breaker state tests |
| `tests/test_stability_refresh.py` | Create | Qdrant payload refresh tests |
| `tests/test_qdrant_indexes.py` | Create | All-collections index tests |

---

### Task 1: Add dead_letter Parameter to MemoryService

**Files:**
- Modify: `backend/src/services/memory_service.py:98-104`
- Create: `backend/tests/test_embedding_dlq.py`

- [ ] **Step 1: Write the failing test for _enqueue_failed_embedding**

```python
# tests/test_embedding_dlq.py
"""Tests for DLQ enqueue on embedding failure."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, make_mock_settings


class TestEnqueueFailedEmbedding:
    @pytest.mark.asyncio
    @patch("src.services.memory_service.get_anthropic_client")
    async def test_enqueues_on_embedding_failure(self, mock_get_client):
        """When embedding returns None, should enqueue to DLQ."""
        from src.services.memory_service import MemoryService

        settings = make_mock_settings()
        mock_db = AsyncMock()
        mock_dlq = AsyncMock()
        mock_dlq.enqueue = AsyncMock(return_value="dlq_test")

        svc = MemoryService(
            settings=settings,
            db=mock_db,
            dead_letter=mock_dlq,
        )

        await svc._enqueue_failed_embedding("mem_test", TEST_USER_ID)

        mock_dlq.enqueue.assert_called_once()
        call_kwargs = mock_dlq.enqueue.call_args
        assert call_kwargs.kwargs["operation_type"] == "failed_embedding"
        assert call_kwargs.kwargs["user_id"] == TEST_USER_ID
        payload = call_kwargs.kwargs["payload"]
        assert payload["record_id"] == "mem_test"
        assert payload["collection"] == "memories"

    @pytest.mark.asyncio
    @patch("src.services.memory_service.get_anthropic_client")
    async def test_no_error_without_dead_letter(self, mock_get_client):
        """When dead_letter is None, should not raise."""
        from src.services.memory_service import MemoryService

        settings = make_mock_settings()
        mock_db = AsyncMock()

        svc = MemoryService(settings=settings, db=mock_db, dead_letter=None)

        await svc._enqueue_failed_embedding("mem_test", TEST_USER_ID)
        # should not raise

    @pytest.mark.asyncio
    @patch("src.services.memory_service.get_anthropic_client")
    async def test_accepts_dead_letter_param(self, mock_get_client):
        """MemoryService.__init__ should accept dead_letter parameter."""
        from src.services.memory_service import MemoryService

        settings = make_mock_settings()
        mock_db = AsyncMock()
        mock_dlq = AsyncMock()

        svc = MemoryService(
            settings=settings, db=mock_db, dead_letter=mock_dlq
        )
        assert svc._dead_letter is mock_dlq
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_embedding_dlq.py -v`
Expected: FAIL — `MemoryService.__init__` doesn't accept `dead_letter`.

- [ ] **Step 3: Add dead_letter param and _enqueue_failed_embedding**

In `backend/src/services/memory_service.py`, update `__init__` (line 98):

```python
    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        event_bus=None,
        vector_store=None,
        dead_letter=None,
    ):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._embedder = EmbeddingService(settings)
        self._event_bus = event_bus
        self._vector_store = vector_store
        self._dead_letter = dead_letter
```

Add the helper method after `__init__`:

```python
    async def _enqueue_failed_embedding(
        self, record_id: str, user_id: str, collection: str = "memories"
    ) -> None:
        """Enqueue a failed embedding for retry via DLQ."""
        if not self._dead_letter:
            return
        try:
            await self._dead_letter.enqueue(
                user_id=user_id,
                operation_type="failed_embedding",
                error_type="EmbeddingFailure",
                error_message=(
                    f"Embedding/upsert failed for {collection}:{record_id}"
                ),
                payload={
                    "record_id": record_id,
                    "collection": collection,
                    "record_type": "memory",
                },
            )
        except Exception:
            logger.warning(
                "Failed to enqueue embedding retry for %s",
                record_id,
                exc_info=True,
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_embedding_dlq.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/memory_service.py tests/test_embedding_dlq.py
git commit -m "feat(6b): add dead_letter param and _enqueue_failed_embedding to MemoryService"
```

---

### Task 2: Replace Silent Embedding Skips with DLQ Enqueue

**Files:**
- Modify: `backend/src/services/memory_service.py` (6 upsert sites)

- [ ] **Step 1: Write the failing test for DLQ on upsert failure**

Add to `tests/test_embedding_dlq.py`:

```python
class TestStoreMemoryDLQ:
    @pytest.mark.asyncio
    @patch("src.services.memory_service.get_anthropic_client")
    async def test_store_memory_enqueues_on_qdrant_failure(self, mock_get_client):
        """store_memory should DLQ when vector_store.upsert fails."""
        from src.services.memory_service import MemoryService

        settings = make_mock_settings()
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        mock_vector_store = AsyncMock()
        mock_vector_store.upsert = AsyncMock(side_effect=Exception("Qdrant down"))

        mock_dlq = AsyncMock()
        mock_dlq.enqueue = AsyncMock(return_value="dlq_1")

        svc = MemoryService(
            settings=settings,
            db=mock_db,
            vector_store=mock_vector_store,
            dead_letter=mock_dlq,
        )
        svc._embedder = AsyncMock()
        svc._embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
        svc._event_bus = None

        # store_memory should not raise even if Qdrant fails
        memory_id = await svc.store_memory(
            user_id=TEST_USER_ID,
            fact_text="Test fact",
            memory_type="semantic",
        )

        assert memory_id is not None
        mock_dlq.enqueue.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.services.memory_service.get_anthropic_client")
    async def test_store_memory_enqueues_on_embed_none(self, mock_get_client):
        """store_memory should DLQ when embedding returns None."""
        from src.services.memory_service import MemoryService

        settings = make_mock_settings()
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        mock_dlq = AsyncMock()
        mock_dlq.enqueue = AsyncMock(return_value="dlq_1")

        svc = MemoryService(
            settings=settings,
            db=mock_db,
            vector_store=AsyncMock(),
            dead_letter=mock_dlq,
        )
        svc._embedder = AsyncMock()
        svc._embedder.embed_text = AsyncMock(return_value=None)
        svc._event_bus = None

        memory_id = await svc.store_memory(
            user_id=TEST_USER_ID,
            fact_text="Test fact",
            memory_type="semantic",
        )

        assert memory_id is not None
        mock_dlq.enqueue.assert_called_once()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_embedding_dlq.py::TestStoreMemoryDLQ -v`
Expected: FAIL — DLQ not called on upsert failure.

- [ ] **Step 3: Update all 6 upsert sites**

Replace the pattern at each site. Example for site 6 (`store_memory`, lines 500-514):

**Before:**
```python
        if self._vector_store and embedding:
            await self._vector_store.upsert(
                "memories",
                memory_id,
                embedding,
                self._build_memory_payload(...),
                user_id,
            )
```

**After:**
```python
        if embedding:
            if self._vector_store:
                try:
                    await self._vector_store.upsert(
                        "memories",
                        memory_id,
                        embedding,
                        self._build_memory_payload(...),
                        user_id,
                    )
                except Exception:
                    logger.debug(
                        "Qdrant upsert failed for %s", memory_id, exc_info=True
                    )
                    await self._enqueue_failed_embedding(memory_id, user_id)
            else:
                await self._enqueue_failed_embedding(memory_id, user_id)
        else:
            await self._enqueue_failed_embedding(memory_id, user_id)
```

Apply the same pattern to all 6 sites:
- `extract_and_store()` (lines 174-188)
- `extract_preferences()` (lines 268-282)
- `store_goal_memory()` (lines 332-347)
- `store_instruction_memory()` (lines 386-400)
- `store_briefing_memory()` (lines 446-460)
- `store_memory()` (lines 500-514)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_embedding_dlq.py -v`
Expected: All PASS.

- [ ] **Step 5: Run existing memory service tests for regression**

Run: `cd backend && python -m pytest tests/test_memory_service.py -v`
Expected: All PASS (existing tests don't pass dead_letter, so it defaults to None).

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/services/memory_service.py tests/test_embedding_dlq.py
git commit -m "feat(6b): replace silent embedding skips with DLQ enqueue (6 sites)"
```

---

### Task 3: Neo4j Circuit Breaker

**Files:**
- Modify: `backend/src/services/graph_engine.py:17-22,62-92`
- Create: `backend/tests/test_neo4j_circuit_breaker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_neo4j_circuit_breaker.py
"""Tests for Neo4j circuit breaker in GraphEngine."""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import make_mock_settings


class TestNeo4jCircuit:
    def test_starts_closed(self):
        from src.services.graph_engine import _Neo4jCircuit

        cb = _Neo4jCircuit()
        assert cb.allow_request() is True
        assert cb._state == "closed"

    def test_opens_after_threshold_failures(self):
        from src.services.graph_engine import _Neo4jCircuit

        cb = _Neo4jCircuit()
        for _ in range(5):
            cb.record_failure()
        assert cb._state == "open"
        assert cb.allow_request() is False

    def test_half_open_after_cooldown(self):
        from src.services.graph_engine import _Neo4jCircuit

        cb = _Neo4jCircuit()
        cb.COOLDOWN_SECONDS = 0.01  # fast cooldown for test
        for _ in range(5):
            cb.record_failure()
        assert cb._state == "open"

        time.sleep(0.02)
        assert cb.allow_request() is True
        assert cb._state == "half_open"

    def test_success_closes_circuit(self):
        from src.services.graph_engine import _Neo4jCircuit

        cb = _Neo4jCircuit()
        for _ in range(5):
            cb.record_failure()
        cb.COOLDOWN_SECONDS = 0.01
        time.sleep(0.02)
        cb.allow_request()  # transitions to half_open

        cb.record_success()
        assert cb._state == "closed"
        assert cb._failures == 0

    def test_failure_in_half_open_reopens(self):
        from src.services.graph_engine import _Neo4jCircuit

        cb = _Neo4jCircuit()
        for _ in range(5):
            cb.record_failure()
        cb.COOLDOWN_SECONDS = 0.01
        time.sleep(0.02)
        cb.allow_request()  # transitions to half_open

        cb.record_failure()
        assert cb._state == "open"


class TestGraphEngineCircuitBreaker:
    @pytest.mark.asyncio
    async def test_sync_entity_skips_when_circuit_open(self):
        """When circuit is open, sync_entity should return immediately."""
        from src.services.graph_engine import GraphEngine

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.neo4j_user = "neo4j"
        settings.neo4j_password = "x"

        engine = GraphEngine(settings)
        mock_driver = AsyncMock()
        engine._driver = mock_driver

        # Force circuit open
        for _ in range(5):
            engine._circuit.record_failure()

        await engine.sync_entity(
            entity_id="ent_1",
            entity_type="person",
            name="Test",
            user_id="usr_1",
        )

        # Driver.session should NOT be called (circuit is open)
        mock_driver.session.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_entity_records_success(self):
        """Successful sync should reset circuit failures."""
        from src.services.graph_engine import GraphEngine

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.neo4j_user = "neo4j"
        settings.neo4j_password = "x"

        engine = GraphEngine(settings)
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = AsyncMock()
        mock_driver.session.return_value = mock_session
        engine._driver = mock_driver

        # Add some failures first
        engine._circuit.record_failure()
        engine._circuit.record_failure()

        await engine.sync_entity(
            entity_id="ent_1",
            entity_type="person",
            name="Test",
            user_id="usr_1",
        )

        assert engine._circuit._failures == 0
        assert engine._circuit._state == "closed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_neo4j_circuit_breaker.py -v`
Expected: FAIL — `_Neo4jCircuit` not defined.

- [ ] **Step 3: Implement _Neo4jCircuit**

At the top of `backend/src/services/graph_engine.py`, before the `GraphEngine` class (after imports, around line 14):

```python
import time


class _Neo4jCircuit:
    """Simple circuit breaker for Neo4j connections."""

    FAILURE_THRESHOLD = 5
    COOLDOWN_SECONDS = 120

    def __init__(self):
        self._failures = 0
        self._state = "closed"  # closed, open, half_open
        self._opened_at: float = 0

    def record_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.FAILURE_THRESHOLD:
            self._state = "open"
            self._opened_at = time.monotonic()

    def allow_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self.COOLDOWN_SECONDS:
                self._state = "half_open"
                return True
            return False
        return True  # half_open: allow one probe
```

Update `GraphEngine.__init__`:

```python
    def __init__(self, settings: Settings):
        self._settings = settings
        self._driver = None
        self._circuit = _Neo4jCircuit()
```

- [ ] **Step 4: Integrate circuit breaker into sync_entity**

Replace `sync_entity` method (lines 62-92):

```python
    async def sync_entity(
        self,
        entity_id: str,
        entity_type: str,
        name: str,
        user_id: str,
        attributes: dict | None = None,
    ) -> None:
        """Upsert an entity node to Neo4j."""
        driver = await self._get_driver()
        if not driver or not self._circuit.allow_request():
            return

        try:
            async with driver.session() as session:
                await session.run(
                    """
                    MERGE (e:Entity {entity_id: $entity_id})
                    SET e.entity_type = $entity_type,
                        e.name = $name,
                        e.user_id = $user_id,
                        e.attributes = $attributes
                    """,
                    entity_id=entity_id,
                    entity_type=entity_type,
                    name=name,
                    user_id=user_id,
                    attributes=json.dumps(attributes or {}, default=str),
                )
            self._circuit.record_success()
        except Exception:
            self._circuit.record_failure()
            logger.warning(
                "Neo4j sync_entity failed for %s", entity_id, exc_info=True
            )
```

Apply the same `if not self._circuit.allow_request(): return` + `self._circuit.record_success()` / `self._circuit.record_failure()` pattern to ALL other methods: `sync_relationship`, `delete_entity`, `traverse`, `traverse_weighted`, `find_path`, `get_related_people`, `get_stale_relationships`, `detect_communities`, `traverse_temporal`. For read methods that return data, ensure the early return returns the appropriate empty value (empty list, empty dict).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_neo4j_circuit_breaker.py -v`
Expected: All PASS.

- [ ] **Step 6: Run existing graph tests for regression**

Run: `cd backend && python -m pytest tests/ -k graph -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/services/graph_engine.py tests/test_neo4j_circuit_breaker.py
git commit -m "feat(6b): add circuit breaker to GraphEngine (5-failure threshold, 120s cooldown)"
```

---

### Task 4: Add set_payload to VectorStore + Extend Indexes

**Files:**
- Modify: `backend/src/services/vector_store.py:99-137,246`
- Create: `backend/tests/test_qdrant_indexes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_qdrant_indexes.py
"""Tests for VectorStore set_payload and extended indexes."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import make_mock_settings


class TestSetPayload:
    @pytest.mark.asyncio
    async def test_updates_payload_fields(self):
        """set_payload should call client.set_payload with correct args."""
        from src.services.vector_store import VectorStore

        settings = make_mock_settings()
        settings.qdrant_url = "http://localhost:6333"

        vs = VectorStore(settings)
        mock_client = AsyncMock()
        vs._client = mock_client

        await vs.set_payload("memories", "mem_test", {"stability_score": 0.4})

        mock_client.set_payload.assert_called_once()
        call_kwargs = mock_client.set_payload.call_args.kwargs
        assert call_kwargs["collection_name"] == "memories"
        assert call_kwargs["payload"] == {"stability_score": 0.4}

    @pytest.mark.asyncio
    async def test_noop_without_client(self):
        """set_payload should no-op when Qdrant not configured."""
        from src.services.vector_store import VectorStore

        settings = make_mock_settings()
        settings.qdrant_url = ""

        vs = VectorStore(settings)
        await vs.set_payload("memories", "mem_test", {"x": 1})  # should not raise


class TestExtendedIndexes:
    @pytest.mark.asyncio
    async def test_ensure_indexes_covers_all_collections(self):
        """ensure_indexes should cover all 6 collections."""
        from src.services.vector_store import VectorStore

        settings = make_mock_settings()
        settings.qdrant_url = "http://localhost:6333"

        vs = VectorStore(settings)
        mock_client = AsyncMock()
        vs._client = mock_client

        await vs.ensure_indexes()

        collection_names = {
            c.kwargs["collection_name"]
            for c in mock_client.create_payload_index.call_args_list
        }
        assert "memories" in collection_names
        assert "entities" in collection_names
        assert "events" in collection_names
        assert "approvals" in collection_names
        assert "conversations" in collection_names
        assert "artifacts" in collection_names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_qdrant_indexes.py -v`
Expected: FAIL — `set_payload` not defined, `approvals` not in indexed collections.

- [ ] **Step 3: Implement set_payload**

Add to `VectorStore` class in `vector_store.py`, after the `delete` method:

```python
    async def set_payload(
        self, collection: str, point_id: str, payload: dict
    ) -> None:
        """Update payload fields on an existing point without re-embedding."""
        client = await self._get_client()
        if not client:
            return
        from qdrant_client.models import PointIdsList

        await client.set_payload(
            collection_name=collection,
            payload=payload,
            points=PointIdsList(points=[_to_qdrant_id(point_id)]),
        )
```

- [ ] **Step 4: Extend ensure_indexes to all 6 collections**

Replace the `indexes` dict in `ensure_indexes` (lines 107-119):

```python
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
            COLLECTION_APPROVALS: [
                ("capability", PayloadSchemaType.KEYWORD),
                ("outcome", PayloadSchemaType.KEYWORD),
            ],
            COLLECTION_CONVERSATIONS: [
                ("conversation_id", PayloadSchemaType.KEYWORD),
            ],
            COLLECTION_ARTIFACTS: [
                ("artifact_type", PayloadSchemaType.KEYWORD),
                ("mime_type", PayloadSchemaType.KEYWORD),
            ],
        }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_qdrant_indexes.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/services/vector_store.py tests/test_qdrant_indexes.py
git commit -m "feat(6b): add set_payload and extend indexes to all 6 Qdrant collections"
```

---

### Task 5: Stability Refresh Scheduler Tick

**Files:**
- Modify: `backend/src/services/scheduler.py`
- Create: `backend/tests/test_stability_refresh.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stability_refresh.py
"""Tests for stability refresh scheduler tick."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import make_mock_settings


class TestTickStabilityRefresh:
    @pytest.mark.asyncio
    async def test_updates_qdrant_payloads_for_stale_memories(self):
        """Should call set_payload for memories with old last_accessed_at."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            ("mem_1", 0.3),
            ("mem_2", 0.1),
        ]
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_ctx)

        mock_vector_store = AsyncMock()
        mock_vector_store.set_payload = AsyncMock()

        await scheduler._tick_stability_refresh(mock_factory, mock_vector_store)

        assert mock_vector_store.set_payload.call_count == 2
        mock_vector_store.set_payload.assert_any_call(
            "memories", "mem_1", {"stability_score": 0.3}
        )
        mock_vector_store.set_payload.assert_any_call(
            "memories", "mem_2", {"stability_score": 0.1}
        )

    @pytest.mark.asyncio
    async def test_skips_when_no_vector_store(self):
        """Should no-op when vector_store is None."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        mock_factory = MagicMock()
        await scheduler._tick_stability_refresh(mock_factory, None)
        # should not raise

    @pytest.mark.asyncio
    async def test_handles_empty_result(self):
        """Should handle no stale memories gracefully."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_ctx)

        mock_vs = AsyncMock()
        await scheduler._tick_stability_refresh(mock_factory, mock_vs)
        mock_vs.set_payload.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_stability_refresh.py -v`
Expected: FAIL — `_tick_stability_refresh` not defined.

- [ ] **Step 3: Implement _tick_stability_refresh**

Add to `SchedulerLoop` in `scheduler.py`:

```python
    async def _tick_stability_refresh(self, factory, vector_store=None) -> None:
        """Batch-update Qdrant stability_score for stale memories."""
        if not vector_store:
            return
        try:
            from datetime import datetime, timedelta, timezone

            from sqlalchemy import select

            from src.models.memory import Memory

            async with factory() as db:
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                result = await db.execute(
                    select(Memory.memory_id, Memory.stability_score)
                    .where(
                        Memory.status == "active",
                        Memory.last_accessed_at < cutoff,
                    )
                    .limit(200)
                )
                updates = result.all()
                if not updates:
                    return

                for memory_id, stability in updates:
                    try:
                        await vector_store.set_payload(
                            "memories",
                            memory_id,
                            {"stability_score": stability or 0.0},
                        )
                    except Exception:
                        pass  # best-effort per record

                logger.info(
                    "Stability refresh: %d Qdrant payloads updated",
                    len(updates),
                )
        except Exception:
            logger.warning("Stability refresh tick failed", exc_info=True)
```

Wire into `_tick()`, in the daily gate alongside consolidation:

```python
        # 4c. Memory consolidation + stability refresh — once daily at ~2 AM UTC
        from datetime import datetime, timezone

        current_hour = datetime.now(timezone.utc).hour
        if self._tick_count % 120 == 0 and current_hour == 2:
            await self._tick_consolidation(factory)
            await self._tick_stability_refresh(factory, vector_store)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_stability_refresh.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/scheduler.py tests/test_stability_refresh.py
git commit -m "feat(6b): add daily stability refresh tick for Qdrant payloads"
```

---

### Task 6: Final Integration + Regression Sweep

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -q 2>&1 | tail -20`
Expected: All PASS.

- [ ] **Step 2: Run ruff format and lint**

Run: `cd backend && ruff format src/ tests/ && ruff check src/ tests/ --fix`
Expected: Clean output.

- [ ] **Step 3: Final commit if needed**

```bash
cd backend
git add -A
git commit -m "chore(6b): lint and format"
```

---

## Summary of Spec Coverage

| Spec Component | Task(s) | Status |
|---------------|---------|--------|
| 1. DLQ for failed embeddings | Tasks 1, 2 | |
| 2. Neo4j circuit breaker | Task 3 | |
| 3. Qdrant payload refresh | Task 5 | |
| 4. Separate extraction from indexing | Task 2 (DLQ handles retry) | |
| 5. Missing Qdrant payload indexes | Task 4 | |
