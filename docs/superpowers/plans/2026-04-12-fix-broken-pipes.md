# Fix the Broken Pipes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire 6 broken/disconnected links in the intelligence pipeline so data flows from creation in Postgres to all target stores (Neo4j, Qdrant).

**Architecture:** Refactor `StreamConsumerManager` to split stream subscriptions (main vs agent events), add `graph_syncer` and `contradiction_checker` consumer groups, persist `GraphSyncService` in runtime, wire inline Neo4j sync for entity tool updates, add Qdrant cascade deletes on memory lifecycle events, and add a direct consolidation scheduler tick.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (auto mode), SQLAlchemy async, Redis Streams, Neo4j AsyncGraphDatabase, Qdrant

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/services/worker.py` | Modify | Split stream groups, add graph_syncer + contradiction_checker handlers |
| `src/runtime.py` | Modify | Persist GraphSyncService as Tier 3 service |
| `src/tools/intelligence_server.py` | Modify | Inline Neo4j sync after update_entity commit |
| `src/services/memory_service.py` | Modify | Qdrant cascade delete in check_contradictions + consolidate_memories |
| `src/services/scheduler.py` | Modify | Add _tick_consolidation daily tick |
| `tests/test_worker_graph_sync.py` | Create | Graph sync consumer tests |
| `tests/test_worker_contradiction.py` | Create | Contradiction consumer tests |
| `tests/test_memory_cascade_delete.py` | Create | Qdrant cascade delete tests |
| `tests/test_consolidation_tick.py` | Create | Scheduler consolidation tests |
| `tests/test_update_entity_sync.py` | Create | update_entity Neo4j sync tests |

---

### Task 1: Wire GraphSyncService Consumer to Worker

**Files:**
- Modify: `backend/src/services/worker.py:38-97`
- Create: `backend/tests/test_worker_graph_sync.py`

This task adds a `graph_syncer` consumer group that subscribes to the agent events stream (`jarvis:agent_events:{user_id}`) and syncs entity changes to Neo4j.

- [ ] **Step 1: Write the failing test for the graph_syncer handler**

```python
# tests/test_worker_graph_sync.py
"""Tests for graph_syncer consumer in StreamConsumerManager."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    s = make_mock_settings()
    s.neo4j_url = "bolt://localhost:7687"
    s.neo4j_user = "neo4j"
    s.neo4j_password = "x"
    s.qdrant_url = ""
    return s


class TestHandleGraphSync:
    @pytest.mark.asyncio
    @patch("src.services.worker.get_session_factory")
    async def test_syncs_entity_to_neo4j(self, mock_factory, settings):
        """graph_syncer handler should call sync_entity_by_id on entity.created."""
        from src.services.worker import StreamConsumerManager

        worker = StreamConsumerManager(settings)

        mock_db = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = MagicMock(return_value=mock_ctx)

        event = MagicMock()
        event.payload = {
            "event_type": "entity.created",
            "entity_id": "ent_test123",
            "user_id": "usr_test",
        }
        event.user_id = "usr_test"

        with patch("src.services.worker.GraphSyncService") as MockGS:
            mock_gs = AsyncMock()
            MockGS.return_value = mock_gs

            await worker._handle_graph_sync(event)

            mock_gs.sync_entity_by_id.assert_called_once_with("ent_test123")
            mock_gs.sync_relationships_for_entity.assert_called_once_with(
                "ent_test123"
            )
            mock_gs.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_non_entity_events(self, settings):
        """graph_syncer should skip events that aren't entity/relationship changes."""
        from src.services.worker import StreamConsumerManager

        worker = StreamConsumerManager(settings)

        event = MagicMock()
        event.payload = {"event_type": "memory.created", "memory_id": "mem_1"}
        event.user_id = "usr_test"

        # Should return without error (no GraphSyncService instantiated)
        await worker._handle_graph_sync(event)

    @pytest.mark.asyncio
    async def test_skips_when_neo4j_not_configured(self):
        """graph_syncer should no-op when neo4j_url is empty."""
        settings = make_mock_settings()
        settings.neo4j_url = ""

        from src.services.worker import StreamConsumerManager

        worker = StreamConsumerManager(settings)

        event = MagicMock()
        event.payload = {
            "event_type": "entity.created",
            "entity_id": "ent_test",
        }
        event.user_id = "usr_test"

        await worker._handle_graph_sync(event)  # should not raise

    @pytest.mark.asyncio
    async def test_skips_when_no_entity_id(self, settings):
        """graph_syncer should skip events without entity_id."""
        from src.services.worker import StreamConsumerManager

        worker = StreamConsumerManager(settings)

        event = MagicMock()
        event.payload = {"event_type": "entity.created"}
        event.user_id = "usr_test"

        await worker._handle_graph_sync(event)  # should not raise
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_worker_graph_sync.py -v`
Expected: FAIL — `StreamConsumerManager` has no `_handle_graph_sync` method.

- [ ] **Step 3: Implement the graph_syncer handler**

In `backend/src/services/worker.py`, add the handler method to `StreamConsumerManager` after `_handle_trigger_evaluation` (after line 302):

```python
    async def _handle_graph_sync(self, event) -> None:
        """Sync entity/relationship changes to Neo4j (agent events stream)."""
        payload = event.payload
        event_type = payload.get("event_type", "")
        if not event_type.startswith(("entity.", "relationship.")):
            return

        entity_id = payload.get("entity_id")
        if not entity_id:
            return

        if not self._settings.neo4j_url:
            return

        from src.models.database import get_session_factory
        from src.services.graph_sync import GraphSyncService

        factory = get_session_factory()
        async with factory() as db:
            graph_sync = GraphSyncService(self._settings, db)
            try:
                await graph_sync.sync_entity_by_id(entity_id)
                await graph_sync.sync_relationships_for_entity(entity_id)
                logger.info("Graph sync for entity %s via event %s", entity_id, event_type)
            except Exception:
                logger.warning(
                    "Graph sync failed for entity %s", entity_id, exc_info=True
                )
            finally:
                await graph_sync.close()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_worker_graph_sync.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Refactor run() to split stream groups**

Replace the `CONSUMER_GROUPS` class attribute and update `run()` in `worker.py`.

Replace lines 38-42:
```python
    # Main event stream consumer groups (jarvis:events:{user_id})
    MAIN_STREAM_GROUPS = (
        "entity_extractor",
        "memory_extractor",
        "trigger_evaluator",
    )
    # Agent event stream consumer groups (jarvis:agent_events:{user_id})
    AGENT_STREAM_GROUPS = (
        "graph_syncer",
    )
```

Replace the handler_map and subscription loop (lines 82-97):
```python
        # Build handler map (covers both streams)
        handler_map = {
            "entity_extractor": self._handle_entity_extraction,
            "memory_extractor": self._handle_memory_extraction,
            "trigger_evaluator": self._handle_trigger_evaluation,
            "graph_syncer": self._handle_graph_sync,
        }

        # Subscribe to main event stream
        for uid in user_ids:
            stream = bus.event_stream(uid)
            for group in self.MAIN_STREAM_GROUPS:
                await bus.create_consumer_group(stream, group)
                task = asyncio.create_task(
                    self._consumer_loop(bus, stream, group, handler_map[group]),
                    name=f"consumer-{uid}-{group}",
                )
                self._tasks.append(task)

        # Subscribe to agent events stream (entity/relationship changes)
        for uid in user_ids:
            agent_stream = f"jarvis:agent_events:{uid}"
            for group in self.AGENT_STREAM_GROUPS:
                await bus.create_consumer_group(agent_stream, group)
                task = asyncio.create_task(
                    self._consumer_loop(
                        bus, agent_stream, group, handler_map[group]
                    ),
                    name=f"consumer-{uid}-{group}",
                )
                self._tasks.append(task)
```

- [ ] **Step 6: Run all worker tests to check no regression**

Run: `cd backend && python -m pytest tests/test_worker_graph_sync.py tests/ -k worker -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/services/worker.py tests/test_worker_graph_sync.py
git commit -m "feat(6a): wire graph_syncer consumer to agent events stream"
```

---

### Task 2: Persist GraphSyncService in Runtime

**Files:**
- Modify: `backend/src/runtime.py:118-124`

- [ ] **Step 1: Add GraphSyncService to Tier 3 in runtime.py**

After the GraphEngine initialization block (line 124), add:

```python
    try:
        if svc.graph_engine:
            from src.services.graph_sync import GraphSyncService

            svc.extras["graph_sync"] = GraphSyncService(settings, db)
    except Exception:
        logger.debug("Tier 3: GraphSyncService unavailable", exc_info=True)
```

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `cd backend && python -m pytest tests/ -v -x --timeout=60 -q 2>&1 | tail -20`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
cd backend
git add src/runtime.py
git commit -m "feat(6a): persist GraphSyncService as Tier 3 service in runtime"
```

---

### Task 3: Wire update_entity Tool to Neo4j

**Files:**
- Modify: `backend/src/tools/intelligence_server.py:231-233`
- Create: `backend/tests/test_update_entity_sync.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_update_entity_sync.py
"""Tests for update_entity Neo4j sync."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestUpdateEntityNeo4jSync:
    @pytest.mark.asyncio
    @patch("src.tools.intelligence_server._get_db")
    async def test_update_entity_syncs_to_neo4j(self, mock_get_db):
        """After updating entity in Postgres, should sync to Neo4j."""
        from tests.conftest import make_mock_settings

        mock_db = AsyncMock()
        mock_entity = MagicMock()
        mock_entity.entity_id = "ent_test"
        mock_entity.attributes = {"role": "investor"}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entity
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_ctx_mgr = MagicMock()
        mock_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx_mgr.__aexit__ = AsyncMock(return_value=False)
        mock_get_db.return_value = mock_ctx_mgr

        mock_ctx = MagicMock()
        mock_settings = make_mock_settings()
        mock_settings.neo4j_url = "bolt://localhost:7687"

        with patch(
            "src.tools.intelligence_server._get_settings",
            return_value=mock_settings,
        ), patch("src.tools.intelligence_server.GraphSyncService") as MockGS:
            mock_gs = AsyncMock()
            MockGS.return_value = mock_gs

            from src.tools.intelligence_server import update_entity

            result = await update_entity(
                entity_id="ent_test",
                ctx=mock_ctx,
                user_id="usr_test",
                attributes='{"role": "ceo"}',
                workspace_id="ws_test",
            )

            assert result["status"] == "updated"
            mock_gs.sync_entity_by_id.assert_called_once_with("ent_test")
            mock_gs.close.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.tools.intelligence_server._get_db")
    async def test_update_entity_skips_neo4j_when_not_configured(self, mock_get_db):
        """Should skip Neo4j sync when neo4j_url is empty."""
        from tests.conftest import make_mock_settings

        mock_db = AsyncMock()
        mock_entity = MagicMock()
        mock_entity.entity_id = "ent_test"
        mock_entity.attributes = {}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_entity
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_ctx_mgr = MagicMock()
        mock_ctx_mgr.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx_mgr.__aexit__ = AsyncMock(return_value=False)
        mock_get_db.return_value = mock_ctx_mgr

        mock_ctx = MagicMock()
        mock_settings = make_mock_settings()
        mock_settings.neo4j_url = ""

        with patch(
            "src.tools.intelligence_server._get_settings",
            return_value=mock_settings,
        ), patch("src.tools.intelligence_server.GraphSyncService") as MockGS:
            from src.tools.intelligence_server import update_entity

            result = await update_entity(
                entity_id="ent_test",
                ctx=mock_ctx,
                user_id="usr_test",
                attributes='{"x": 1}',
                workspace_id="ws_test",
            )

            assert result["status"] == "updated"
            MockGS.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_update_entity_sync.py -v`
Expected: FAIL — `GraphSyncService` not imported or called in update_entity.

- [ ] **Step 3: Implement inline Neo4j sync in update_entity**

In `backend/src/tools/intelligence_server.py`, replace lines 231-233:

```python
            await db.flush()
            await db.commit()

            # Sync to Neo4j (inline, best-effort)
            try:
                settings = _get_settings()
                if settings.neo4j_url:
                    from src.services.graph_sync import GraphSyncService

                    gs = GraphSyncService(settings, db)
                    await gs.sync_entity_by_id(entity_id)
                    await gs.close()
            except Exception:
                logger.debug(
                    "Neo4j sync after update_entity failed for %s",
                    entity_id,
                    exc_info=True,
                )

            return {"status": "updated", "entity_id": entity_id}
```

Also ensure `_get_settings` is available. Check if it exists — if not, add a helper at the top of the file that returns the Settings singleton. The MCP tools usually access settings via `ctx` — check the file for the pattern. If a `_get_settings()` helper doesn't exist, inline:

```python
            try:
                if hasattr(ctx, "request_context"):
                    settings = ctx.request_context.lifespan_context.get("settings")
                else:
                    from src.config.settings import get_settings
                    settings = get_settings()
                if settings and settings.neo4j_url:
                    from src.services.graph_sync import GraphSyncService
                    gs = GraphSyncService(settings, db)
                    await gs.sync_entity_by_id(entity_id)
                    await gs.close()
            except Exception:
                logger.debug(
                    "Neo4j sync after update_entity failed for %s",
                    entity_id,
                    exc_info=True,
                )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_update_entity_sync.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/tools/intelligence_server.py tests/test_update_entity_sync.py
git commit -m "feat(6a): wire update_entity tool to sync entities to Neo4j"
```

---

### Task 4: Wire Contradiction Checker Consumer

**Files:**
- Modify: `backend/src/services/worker.py`
- Create: `backend/tests/test_worker_contradiction.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_contradiction.py
"""Tests for contradiction_checker consumer in StreamConsumerManager."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


class TestHandleContradictionCheck:
    @pytest.mark.asyncio
    @patch("src.services.worker.get_session_factory")
    async def test_calls_check_contradictions(self, mock_factory, settings):
        """contradiction_checker should call MemoryService.check_contradictions."""
        from src.services.worker import StreamConsumerManager

        worker = StreamConsumerManager(settings)

        mock_db = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = MagicMock(return_value=mock_ctx)

        event = MagicMock()
        event.payload = {
            "memory_id": "mem_new",
            "fact_text": "Alice is the CEO",
            "user_id": "usr_test",
            "workspace_id": "ws_test",
        }
        event.user_id = "usr_test"

        with patch("src.services.worker.MemoryService") as MockMS:
            mock_ms = AsyncMock()
            mock_ms.check_contradictions = AsyncMock(return_value=["mem_old"])
            MockMS.return_value = mock_ms

            await worker._handle_contradiction_check(event)

            mock_ms.check_contradictions.assert_called_once_with(
                user_id="usr_test",
                new_fact="Alice is the CEO",
                new_memory_id="mem_new",
                workspace_id="ws_test",
            )
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_no_memory_id(self, settings):
        """Should skip when payload has no memory_id."""
        from src.services.worker import StreamConsumerManager

        worker = StreamConsumerManager(settings)

        event = MagicMock()
        event.payload = {"fact_text": "some fact"}
        event.user_id = "usr_test"

        await worker._handle_contradiction_check(event)  # should not raise

    @pytest.mark.asyncio
    async def test_skips_when_no_fact_text(self, settings):
        """Should skip when payload has no fact_text."""
        from src.services.worker import StreamConsumerManager

        worker = StreamConsumerManager(settings)

        event = MagicMock()
        event.payload = {"memory_id": "mem_1"}
        event.user_id = "usr_test"

        await worker._handle_contradiction_check(event)  # should not raise
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_worker_contradiction.py -v`
Expected: FAIL — `_handle_contradiction_check` not defined.

- [ ] **Step 3: Implement the contradiction checker handler**

Add to `StreamConsumerManager` in `worker.py`:

```python
    async def _handle_contradiction_check(self, event) -> None:
        """Check if a newly stored memory contradicts existing ones."""
        payload = event.payload
        memory_id = payload.get("memory_id", "")
        fact_text = payload.get("fact_text", "")
        user_id = payload.get("user_id", event.user_id)
        workspace_id = payload.get("workspace_id", "")

        if not memory_id or not fact_text:
            return

        from src.models.database import get_session_factory
        from src.services.memory_service import MemoryService

        factory = get_session_factory()
        async with factory() as db:
            ms = MemoryService(
                settings=self._settings,
                db=db,
                vector_store=self._vector_store,
            )
            superseded = await ms.check_contradictions(
                user_id=user_id,
                new_fact=fact_text,
                new_memory_id=memory_id,
                workspace_id=workspace_id,
            )
            await db.commit()
            if superseded:
                logger.info(
                    "Contradiction check for %s: %d memories superseded",
                    memory_id,
                    len(superseded),
                )
```

Also add `contradiction_checker` to `MAIN_STREAM_GROUPS` and `handler_map`:

```python
    MAIN_STREAM_GROUPS = (
        "entity_extractor",
        "memory_extractor",
        "trigger_evaluator",
        "contradiction_checker",
    )
```

In `run()` handler_map:
```python
        handler_map = {
            "entity_extractor": self._handle_entity_extraction,
            "memory_extractor": self._handle_memory_extraction,
            "trigger_evaluator": self._handle_trigger_evaluation,
            "contradiction_checker": self._handle_contradiction_check,
            "graph_syncer": self._handle_graph_sync,
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_worker_contradiction.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/worker.py tests/test_worker_contradiction.py
git commit -m "feat(6a): wire contradiction_checker consumer to main event stream"
```

---

### Task 5: Qdrant Cascade Delete on Memory Lifecycle

**Files:**
- Modify: `backend/src/services/memory_service.py:642-660,757`
- Create: `backend/tests/test_memory_cascade_delete.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory_cascade_delete.py
"""Tests for Qdrant cascade delete on memory supersede and merge."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, make_mock_settings


class TestCheckContradictionsCascadeDelete:
    @pytest.mark.asyncio
    @patch("src.services.memory_service.get_anthropic_client")
    async def test_superseded_memory_deleted_from_qdrant(self, mock_get_client):
        """When a memory is superseded, it should be deleted from Qdrant."""
        from src.services.memory_service import MemoryService

        settings = make_mock_settings()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.flush = AsyncMock()

        mock_vector_store = AsyncMock()
        mock_vector_store.find_similar = AsyncMock(return_value=[
            {
                "id": "mem_old",
                "payload": {
                    "_original_id": "mem_old",
                    "fact_text": "Alice is the CTO",
                },
                "score": 0.85,
            }
        ])
        mock_vector_store.delete = AsyncMock()

        svc = MemoryService(
            settings=settings,
            db=mock_db,
            vector_store=mock_vector_store,
        )
        svc._embedder = AsyncMock()
        svc._embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
        svc._check_contradiction_pair = AsyncMock(return_value=True)
        svc._event_bus = None

        superseded = await svc.check_contradictions(
            user_id=TEST_USER_ID,
            new_fact="Alice is the CEO",
            new_memory_id="mem_new",
        )

        assert superseded == ["mem_old"]
        mock_vector_store.delete.assert_called_once_with("memories", "mem_old")


class TestConsolidateMemoriesCascadeDelete:
    @pytest.mark.asyncio
    @patch("src.services.memory_service.get_anthropic_client")
    async def test_merged_memory_deleted_from_qdrant(self, mock_get_client):
        """When a memory is merged, it should be deleted from Qdrant."""
        from src.services.memory_service import MemoryService

        settings = make_mock_settings()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Create two similar memories
        mem_keep = MagicMock()
        mem_keep.memory_id = "mem_keep"
        mem_keep.fact_text = "Alice works at Acme"
        mem_keep.confidence = 0.9
        mem_keep.stability_score = 0.5
        mem_keep.status = "active"

        mem_dup = MagicMock()
        mem_dup.memory_id = "mem_dup"
        mem_dup.fact_text = "Alice works at Acme Corp"
        mem_dup.confidence = 0.7
        mem_dup.stability_score = 0.3
        mem_dup.status = "active"

        mock_db = AsyncMock()
        # First execute: list all active memories
        mock_result_all = MagicMock()
        mock_result_all.scalars.return_value.all.return_value = [mem_keep, mem_dup]
        # Second execute: find the duplicate by ID
        mock_result_dup = MagicMock()
        mock_result_dup.scalar_one_or_none.return_value = mem_dup

        mock_db.execute = AsyncMock(
            side_effect=[mock_result_all, mock_result_dup]
        )
        mock_db.flush = AsyncMock()

        mock_vector_store = AsyncMock()
        mock_vector_store.find_similar = AsyncMock(return_value=[
            {
                "id": "mem_dup",
                "payload": {"_original_id": "mem_dup", "fact_text": "Alice works at Acme Corp"},
                "score": 0.97,
            }
        ])
        mock_vector_store.delete = AsyncMock()

        svc = MemoryService(
            settings=settings,
            db=mock_db,
            vector_store=mock_vector_store,
        )
        svc._embedder = AsyncMock()
        svc._embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
        svc._event_bus = None

        merged = await svc.consolidate_memories(TEST_USER_ID)

        assert merged == 1
        assert mem_dup.status == "merged"
        mock_vector_store.delete.assert_called_once_with("memories", "mem_dup")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_memory_cascade_delete.py -v`
Expected: FAIL — `vector_store.delete` not called in either method.

- [ ] **Step 3: Add Qdrant cascade delete in check_contradictions**

In `backend/src/services/memory_service.py`, after line 651 (`superseded.append(cand_id)`), add:

```python
                superseded.append(cand_id)
                # Cascade delete from Qdrant
                if self._vector_store:
                    try:
                        await self._vector_store.delete("memories", cand_id)
                    except Exception:
                        logger.debug(
                            "Qdrant cascade delete failed for superseded %s",
                            cand_id,
                            exc_info=True,
                        )
```

- [ ] **Step 4: Add Qdrant cascade delete in consolidate_memories**

In `backend/src/services/memory_service.py`, after line 757 (`duplicate.status = "merged"`), add:

```python
                duplicate.status = "merged"
                merged_ids.add(duplicate.memory_id)
                merged_count += 1
                # Cascade delete from Qdrant
                if self._vector_store:
                    try:
                        await self._vector_store.delete(
                            "memories", duplicate.memory_id
                        )
                    except Exception:
                        logger.debug(
                            "Qdrant cascade delete failed for merged %s",
                            duplicate.memory_id,
                            exc_info=True,
                        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_memory_cascade_delete.py -v`
Expected: All PASS.

- [ ] **Step 6: Run existing memory service tests for regression**

Run: `cd backend && python -m pytest tests/test_memory_service.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/services/memory_service.py tests/test_memory_cascade_delete.py
git commit -m "feat(6a): cascade delete from Qdrant on memory supersede and merge"
```

---

### Task 6: Enable Memory Consolidation Scheduler Tick

**Files:**
- Modify: `backend/src/services/scheduler.py:89-90`
- Create: `backend/tests/test_consolidation_tick.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_consolidation_tick.py
"""Tests for nightly memory consolidation scheduler tick."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_mock_settings


class TestTickConsolidation:
    @pytest.mark.asyncio
    async def test_consolidates_for_all_active_users(self):
        """_tick_consolidation should run for every user with active memories."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        mock_db = AsyncMock()
        # Mock distinct user_ids query
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = [("usr_1",), ("usr_2",)]
        mock_db.execute = AsyncMock(return_value=mock_user_result)
        mock_db.commit = AsyncMock()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_ctx)

        with patch("src.services.scheduler.MemoryService") as MockMS:
            mock_ms = AsyncMock()
            mock_ms.consolidate_memories = AsyncMock(return_value=3)
            MockMS.return_value = mock_ms

            await scheduler._tick_consolidation(mock_factory)

            assert mock_ms.consolidate_memories.call_count == 2
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_no_active_users(self):
        """_tick_consolidation should handle empty user list gracefully."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        mock_db = AsyncMock()
        mock_user_result = MagicMock()
        mock_user_result.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_user_result)
        mock_db.commit = AsyncMock()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_ctx)

        await scheduler._tick_consolidation(mock_factory)  # should not raise

    @pytest.mark.asyncio
    async def test_logs_warning_on_failure(self):
        """_tick_consolidation should log warning on exception."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        mock_factory = MagicMock(side_effect=Exception("db down"))

        # Should not raise
        await scheduler._tick_consolidation(mock_factory)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_consolidation_tick.py -v`
Expected: FAIL — `_tick_consolidation` not defined.

- [ ] **Step 3: Implement _tick_consolidation**

Add to `SchedulerLoop` in `backend/src/services/scheduler.py`:

```python
    async def _tick_consolidation(self, factory) -> None:
        """Nightly memory consolidation — merge highly similar memories."""
        try:
            async with factory() as db:
                from sqlalchemy import distinct, select

                from src.models.memory import Memory
                from src.services.memory_service import MemoryService

                result = await db.execute(
                    select(distinct(Memory.user_id)).where(
                        Memory.status == "active"
                    )
                )
                user_ids = [r[0] for r in result.all()]

                total_merged = 0
                for uid in user_ids:
                    ms = MemoryService(
                        settings=self._settings, db=db,
                    )
                    merged = await ms.consolidate_memories(uid)
                    total_merged += merged

                await db.commit()
                if total_merged:
                    logger.info(
                        "Nightly consolidation: %d memories merged",
                        total_merged,
                    )
        except Exception:
            logger.warning("Memory consolidation tick failed", exc_info=True)
```

- [ ] **Step 4: Wire into _tick method**

In `scheduler.py`, after the persona batch line (line 90), add:

```python
        # 4b. Persona batch — every 10th tick (~5 min)
        await self._tick_persona_batch()

        # 4c. Memory consolidation — once daily at ~2 AM UTC
        from datetime import datetime, timezone

        current_hour = datetime.now(timezone.utc).hour
        if self._tick_count % 120 == 0 and current_hour == 2:
            await self._tick_consolidation(factory)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_consolidation_tick.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/services/scheduler.py tests/test_consolidation_tick.py
git commit -m "feat(6a): add nightly memory consolidation scheduler tick"
```

---

### Task 7: Final Integration + Regression Sweep

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -q 2>&1 | tail -20`
Expected: All PASS.

- [ ] **Step 2: Run ruff format and lint**

Run: `cd backend && ruff format src/ tests/ && ruff check src/ tests/ --fix`
Expected: Clean output.

- [ ] **Step 3: Final commit if any formatting needed**

```bash
cd backend
git add -A
git commit -m "chore(6a): lint and format"
```

---

## Summary of Spec Coverage

| Spec Component | Task(s) | Status |
|---------------|---------|--------|
| 1. Wire GraphSyncService event handlers | Task 1 | |
| 2. Persist GraphSyncService in Runtime | Task 2 | |
| 3. Wire update_entity to Neo4j | Task 3 | |
| 4. Wire Contradiction Checker consumer | Task 4 | |
| 5. Qdrant cascade delete on memory lifecycle | Task 5 | |
| 6. Enable memory consolidation tick | Task 6 | |
