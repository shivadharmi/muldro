# Observability + Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make system health visible (log levels, health endpoint, sync metrics), remove dead code (`procedural` memory type), and validate Tier 3 service configuration at startup.

**Architecture:** Elevate silent failure log levels from DEBUG to WARNING. Add `health()` and `get_metrics()` to `GraphEngine` and `VectorStore`. Store persistent instances on `app.state` during lifespan startup. Add `GET /v1/health/stores` endpoint. Remove `procedural` memory type from schema. Add startup validation for configured-but-failed Tier 3 services.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (auto mode), FastAPI, Alembic, ruff

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/services/scheduler.py` | Modify | Elevate 3 log levels (DLQ retry, DLQ enqueue, notification) |
| `src/runtime.py` | Modify | Elevate VectorStore log, add validate_tier3_health() |
| `src/services/worker.py` | Modify | Elevate DLQ enqueue log |
| `src/services/graph_engine.py` | Modify | Add health(), _metrics dict, get_metrics() |
| `src/services/vector_store.py` | Modify | Add health(), _metrics dict, get_metrics() |
| `src/api/app.py` | Modify | Store graph_engine + vector_store on app.state |
| `src/api/routes_health.py` | Modify | Add GET /v1/health/stores endpoint |
| `src/models/memory.py` | Modify | Remove procedural from comment |
| `src/api/schemas.py` | Modify | Remove procedural from MemoryType Literal |
| `src/api/schemas/__init__.py` | Modify | Remove procedural from MemoryType Literal |
| `alembic/versions/xxx_remove_procedural.py` | Create | Migration to reclassify rows |
| `tests/test_health_stores.py` | Create | Health endpoint tests |
| `tests/test_graph_metrics.py` | Create | GraphEngine metrics + health tests |
| `tests/test_startup_validation.py` | Create | Tier 3 validation tests |

---

### Task 1: Elevate DLQ and Silent Failure Log Levels

**Files:**
- Modify: `backend/src/services/scheduler.py:516,419-422,703`
- Modify: `backend/src/runtime.py:116`
- Modify: `backend/src/services/worker.py:164-165`

- [ ] **Step 1: Elevate scheduler.py DLQ retry tick (line 516)**

Change:
```python
        except Exception:
            logger.debug("DLQ retry tick failed", exc_info=True)
```
To:
```python
        except Exception:
            logger.warning("DLQ retry tick failed", exc_info=True)
```

- [ ] **Step 2: Elevate scheduler.py notification tick (line 703)**

Find the pending notification tick failure and change `logger.debug` to `logger.info`:
```python
        except Exception:
            logger.info("Pending notification tick failed", exc_info=True)
```

- [ ] **Step 3: Elevate runtime.py VectorStore init (line 116)**

Change:
```python
    except Exception:
        logger.debug("Tier 3: VectorStore unavailable", exc_info=True)
```
To:
```python
    except Exception:
        logger.warning(
            "Tier 3: VectorStore unavailable — semantic search and embedding disabled",
            exc_info=True,
        )
```

- [ ] **Step 4: Run existing tests to verify no regression**

Run: `cd backend && python -m pytest tests/ -v -x --timeout=60 -q 2>&1 | tail -10`
Expected: All PASS (log level changes don't affect behavior).

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/scheduler.py src/runtime.py src/services/worker.py
git commit -m "fix(6c): elevate DLQ and silent failure log levels to WARNING"
```

---

### Task 2: GraphEngine health() and Sync Metrics

**Files:**
- Modify: `backend/src/services/graph_engine.py`
- Create: `backend/tests/test_graph_metrics.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph_metrics.py
"""Tests for GraphEngine health check and sync metrics."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import make_mock_settings


class TestGraphEngineHealth:
    @pytest.mark.asyncio
    async def test_health_returns_disabled_when_not_configured(self):
        """health() should return disabled when neo4j_url is empty."""
        from src.services.graph_engine import GraphEngine

        settings = make_mock_settings()
        settings.neo4j_url = ""
        engine = GraphEngine(settings)

        result = await engine.health()
        assert result["status"] == "disabled"
        assert result["configured"] is False

    @pytest.mark.asyncio
    async def test_health_returns_healthy_when_connected(self):
        """health() should return healthy when RETURN 1 succeeds."""
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

        result = await engine.health()
        assert result["status"] == "healthy"
        assert result["configured"] is True
        assert "circuit_state" in result

    @pytest.mark.asyncio
    async def test_health_returns_unreachable_on_error(self):
        """health() should return unreachable when probe fails."""
        from src.services.graph_engine import GraphEngine

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.neo4j_user = "neo4j"
        settings.neo4j_password = "x"

        engine = GraphEngine(settings)
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.run = AsyncMock(side_effect=Exception("Connection refused"))

        mock_driver = AsyncMock()
        mock_driver.session.return_value = mock_session
        engine._driver = mock_driver

        result = await engine.health()
        assert result["status"] == "unreachable"
        assert "error" in result


class TestGraphEngineMetrics:
    @pytest.mark.asyncio
    async def test_sync_entity_increments_success(self):
        """Successful sync should increment success counter."""
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

        await engine.sync_entity("ent_1", "person", "Test", "usr_1")

        metrics = engine.get_metrics()
        assert metrics["sync_success"] == 1
        assert metrics["sync_failure"] == 0

    @pytest.mark.asyncio
    async def test_sync_entity_increments_failure(self):
        """Failed sync should increment failure counter."""
        from src.services.graph_engine import GraphEngine

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.neo4j_user = "neo4j"
        settings.neo4j_password = "x"

        engine = GraphEngine(settings)
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.run = AsyncMock(side_effect=Exception("timeout"))

        mock_driver = AsyncMock()
        mock_driver.session.return_value = mock_session
        engine._driver = mock_driver

        await engine.sync_entity("ent_1", "person", "Test", "usr_1")

        metrics = engine.get_metrics()
        assert metrics["sync_failure"] == 1
        assert metrics["last_failure_error"] is not None

    def test_get_metrics_returns_circuit_state(self):
        """get_metrics should include circuit_state."""
        from src.services.graph_engine import GraphEngine

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        engine = GraphEngine(settings)

        metrics = engine.get_metrics()
        assert metrics["circuit_state"] == "closed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_graph_metrics.py -v`
Expected: FAIL — `health()`, `get_metrics()` not defined.

- [ ] **Step 3: Implement health(), _metrics, get_metrics()**

In `backend/src/services/graph_engine.py`, update `__init__`:

```python
    def __init__(self, settings: Settings):
        self._settings = settings
        self._driver = None
        self._circuit = _Neo4jCircuit()
        self._metrics = {
            "sync_success": 0,
            "sync_failure": 0,
            "last_failure_at": None,
            "last_failure_error": None,
        }
```

Add `health()` method:

```python
    async def health(self) -> dict:
        """Lightweight health check — runs RETURN 1."""
        if not self._settings.neo4j_url:
            return {"status": "disabled", "configured": False}
        try:
            driver = await self._get_driver()
            if not driver:
                return {"status": "unreachable", "configured": True}
            async with driver.session() as session:
                await session.run("RETURN 1")
            return {
                "status": "healthy",
                "configured": True,
                "circuit_state": self._circuit._state,
            }
        except Exception as exc:
            return {
                "status": "unreachable",
                "configured": True,
                "error": str(exc)[:200],
            }
```

Add `get_metrics()` method:

```python
    def get_metrics(self) -> dict:
        """Return sync metrics and circuit state."""
        return {**self._metrics, "circuit_state": self._circuit._state}
```

Update `sync_entity` (and all other methods) to increment metrics:

```python
        try:
            async with driver.session() as session:
                await session.run(...)
            self._circuit.record_success()
            self._metrics["sync_success"] += 1
        except Exception:
            self._circuit.record_failure()
            self._metrics["sync_failure"] += 1
            self._metrics["last_failure_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            self._metrics["last_failure_error"] = str(exc)[:200]
            logger.warning(...)
```

Add the import at the top:
```python
from datetime import datetime, timezone
```

Apply the same `_metrics["sync_success"] += 1` / `_metrics["sync_failure"] += 1` pattern to all 11 methods.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_graph_metrics.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/graph_engine.py tests/test_graph_metrics.py
git commit -m "feat(6c): add health(), get_metrics() to GraphEngine with sync counters"
```

---

### Task 3: VectorStore health() and Metrics

**Files:**
- Modify: `backend/src/services/vector_store.py`

- [ ] **Step 1: Add _metrics to VectorStore.__init__**

```python
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = None
        self._metrics = {
            "upsert_success": 0,
            "upsert_failure": 0,
            "delete_success": 0,
            "delete_failure": 0,
        }
```

- [ ] **Step 2: Add health() and get_metrics() methods**

```python
    async def health(self) -> dict:
        """Lightweight health check — lists collections."""
        if not self._settings.qdrant_url:
            return {"status": "disabled", "configured": False}
        try:
            client = await self._get_client()
            if not client:
                return {"status": "unreachable", "configured": True}
            collections = await client.get_collections()
            return {
                "status": "healthy",
                "configured": True,
                "collections": len(collections.collections),
            }
        except Exception as exc:
            return {
                "status": "unreachable",
                "configured": True,
                "error": str(exc)[:200],
            }

    def get_metrics(self) -> dict:
        """Return upsert/delete metrics."""
        return dict(self._metrics)
```

- [ ] **Step 3: Increment counters in upsert and delete**

In `upsert()`, wrap the client call:
```python
        try:
            await client.upsert(...)
            self._metrics["upsert_success"] += 1
        except Exception:
            self._metrics["upsert_failure"] += 1
            raise
```

In `delete()`, wrap:
```python
        try:
            await client.delete(...)
            self._metrics["delete_success"] += 1
        except Exception:
            self._metrics["delete_failure"] += 1
            raise
```

- [ ] **Step 4: Run existing tests for regression**

Run: `cd backend && python -m pytest tests/ -k "vector" -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/vector_store.py
git commit -m "feat(6c): add health(), get_metrics() to VectorStore with counters"
```

---

### Task 4: Store Persistent Instances on app.state

**Files:**
- Modify: `backend/src/api/app.py:165-173`

- [ ] **Step 1: Update lifespan to store instances on app.state**

In `app.py`, replace the VectorStore/Qdrant init block (lines 165-173):

```python
        # Ensure Qdrant collections exist + store persistent instance
        try:
            from src.services.vector_store import VectorStore

            vector_store = VectorStore(settings)
            await vector_store.ensure_collections()
            await vector_store.ensure_indexes()
            app.state.vector_store = vector_store
        except Exception:
            logger.debug("Qdrant collection init skipped", exc_info=True)
            app.state.vector_store = None
```

Add GraphEngine to app.state (after the VectorStore block):

```python
        # Initialize GraphEngine + store persistent instance
        try:
            from src.services.graph_engine import GraphEngine

            if settings.neo4j_url:
                app.state.graph_engine = GraphEngine(settings)
            else:
                app.state.graph_engine = None
        except Exception:
            logger.debug("GraphEngine init skipped", exc_info=True)
            app.state.graph_engine = None
```

- [ ] **Step 2: Run existing tests for regression**

Run: `cd backend && python -m pytest tests/ -v -x --timeout=60 -q 2>&1 | tail -10`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
cd backend
git add src/api/app.py
git commit -m "feat(6c): store persistent GraphEngine + VectorStore on app.state"
```

---

### Task 5: Health Stores Endpoint

**Files:**
- Modify: `backend/src/api/routes_health.py`
- Create: `backend/tests/test_health_stores.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_health_stores.py
"""Tests for GET /v1/health/stores endpoint."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestHealthStoresEndpoint:
    @pytest.mark.asyncio
    async def test_all_disabled(self):
        """Returns disabled for all stores when none configured."""
        from src.api.routes_health import _build_store_health
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        settings.neo4j_url = ""
        settings.qdrant_url = ""
        settings.redis_url = ""

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=None,
            db=AsyncMock(),
        )

        assert result["neo4j"]["status"] == "disabled"
        assert result["qdrant"]["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_healthy_graph_engine(self):
        """Returns healthy with metrics when GraphEngine is connected."""
        from src.api.routes_health import _build_store_health
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"

        mock_ge = AsyncMock()
        mock_ge.health = AsyncMock(return_value={
            "status": "healthy",
            "configured": True,
            "circuit_state": "closed",
        })
        mock_ge.get_metrics = MagicMock(return_value={
            "sync_success": 10,
            "sync_failure": 1,
            "circuit_state": "closed",
        })

        result = await _build_store_health(
            settings=settings,
            graph_engine=mock_ge,
            vector_store=None,
            redis=None,
            db=AsyncMock(),
        )

        assert result["neo4j"]["status"] == "healthy"
        assert result["neo4j"]["sync_stats"]["sync_success"] == 10

    @pytest.mark.asyncio
    async def test_configured_but_unreachable_shows_in_degraded(self):
        """Configured but failed services appear in degraded_services."""
        from src.api.routes_health import _build_store_health
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.qdrant_url = "http://localhost:6333"

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,  # configured but failed to init
            vector_store=None,
            redis=None,
            db=AsyncMock(),
        )

        assert "neo4j" in result["degraded_services"]
        assert "qdrant" in result["degraded_services"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_health_stores.py -v`
Expected: FAIL — `_build_store_health` not defined.

- [ ] **Step 3: Implement the endpoint**

In `backend/src/api/routes_health.py`, add the helper and route:

```python
async def _build_store_health(
    settings,
    graph_engine,
    vector_store,
    redis,
    db,
) -> dict:
    """Build health status for all data stores."""
    # Neo4j
    if graph_engine:
        neo4j_health = await graph_engine.health()
        neo4j_health["sync_stats"] = graph_engine.get_metrics()
    elif settings.neo4j_url:
        neo4j_health = {
            "status": "unreachable",
            "configured": True,
            "error": "GraphEngine failed to initialize at startup",
        }
    else:
        neo4j_health = {"status": "disabled", "configured": False}

    # Qdrant
    if vector_store:
        qdrant_health = await vector_store.health()
        qdrant_health["metrics"] = vector_store.get_metrics()
    elif settings.qdrant_url:
        qdrant_health = {
            "status": "unreachable",
            "configured": True,
            "error": "VectorStore failed to initialize at startup",
        }
    else:
        qdrant_health = {"status": "disabled", "configured": False}

    # Postgres
    postgres_health: dict = {"status": "healthy"}
    try:
        from sqlalchemy import func, select

        from src.models.dead_letter import DeadLetterEntry

        result = await db.execute(
            select(func.count()).where(
                DeadLetterEntry.status.in_(["pending", "retrying"])
            )
        )
        postgres_health["pending_dlq"] = result.scalar() or 0
    except Exception:
        postgres_health = {"status": "unreachable"}

    # Redis
    if redis:
        try:
            await redis.ping()
            redis_health: dict = {"status": "healthy"}
        except Exception:
            redis_health = {"status": "unreachable"}
    elif settings.redis_url:
        redis_health = {"status": "unreachable", "error": "Redis failed to initialize"}
    else:
        redis_health = {"status": "disabled"}

    # Degraded services
    degraded = []
    if neo4j_health.get("configured") and neo4j_health["status"] != "healthy":
        degraded.append("neo4j")
    if qdrant_health.get("configured") and qdrant_health["status"] != "healthy":
        degraded.append("qdrant")

    return {
        "neo4j": neo4j_health,
        "qdrant": qdrant_health,
        "postgres": postgres_health,
        "redis": redis_health,
        "degraded_services": degraded,
    }


@router.get("/v1/health/stores")
async def health_stores(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    """Data store health with sync metrics and degradation status."""
    graph_engine = getattr(request.app.state, "graph_engine", None)
    vector_store = getattr(request.app.state, "vector_store", None)
    redis = getattr(request.app.state, "redis", None)

    return await _build_store_health(
        settings=settings,
        graph_engine=graph_engine,
        vector_store=vector_store,
        redis=redis,
        db=db,
    )
```

Add required imports at the top of `routes_health.py`:
```python
from starlette.requests import Request
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_health_stores.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/api/routes_health.py tests/test_health_stores.py
git commit -m "feat(6c): add GET /v1/health/stores endpoint with sync metrics"
```

---

### Task 6: Remove Procedural Memory Type

**Files:**
- Modify: `backend/src/models/memory.py:19`
- Modify: `backend/src/api/schemas.py:10-12`
- Modify: `backend/src/api/schemas/__init__.py:10-12`

- [ ] **Step 1: Remove from models/memory.py comment**

Change line 19:
```python
    # episodic, semantic, preference, relationship, task_context, procedural
```
To:
```python
    # episodic, semantic, preference, relationship, task_context
```

- [ ] **Step 2: Remove from api/schemas.py**

Change lines 10-12:
```python
MemoryType = Literal[
    "episodic", "semantic", "preference", "relationship", "task_context", "procedural"
]
```
To:
```python
MemoryType = Literal[
    "episodic", "semantic", "preference", "relationship", "task_context"
]
```

- [ ] **Step 3: Remove from api/schemas/__init__.py**

Same change as Step 2.

- [ ] **Step 4: Create Alembic migration**

Run: `cd backend && alembic revision -m "reclassify procedural memories as task_context"`

Edit the generated migration file:

```python
"""reclassify procedural memories as task_context"""

from alembic import op


def upgrade() -> None:
    op.execute(
        "UPDATE memories SET memory_type = 'task_context' "
        "WHERE memory_type = 'procedural'"
    )


def downgrade() -> None:
    pass  # no-op: procedural was never created
```

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `cd backend && python -m pytest tests/ -v -x --timeout=60 -q 2>&1 | tail -10`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/models/memory.py src/api/schemas.py src/api/schemas/__init__.py alembic/versions/
git commit -m "fix(6c): remove procedural memory type, reclassify as task_context"
```

---

### Task 7: Startup Validation for Tier 3 Services

**Files:**
- Modify: `backend/src/runtime.py:261-292`
- Create: `backend/tests/test_startup_validation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_startup_validation.py
"""Tests for Tier 3 startup validation."""

import pytest
from unittest.mock import MagicMock

from tests.conftest import make_mock_settings


class TestValidateTier3Health:
    def test_detects_configured_but_missing_neo4j(self):
        """Should flag neo4j as degraded when URL is set but engine is None."""
        from src.runtime import validate_tier3_health

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.qdrant_url = ""
        settings.reranker_enabled = False

        svc = MagicMock()
        svc.graph_engine = None
        svc.vector_store = MagicMock()
        svc.reranker = None
        svc.extras = {}

        degraded = validate_tier3_health(settings, svc)
        assert "neo4j" in degraded

    def test_detects_configured_but_missing_qdrant(self):
        """Should flag qdrant when URL is set but store is None."""
        from src.runtime import validate_tier3_health

        settings = make_mock_settings()
        settings.neo4j_url = ""
        settings.qdrant_url = "http://localhost:6333"
        settings.reranker_enabled = False

        svc = MagicMock()
        svc.graph_engine = None
        svc.vector_store = None
        svc.reranker = None
        svc.extras = {}

        degraded = validate_tier3_health(settings, svc)
        assert "qdrant" in degraded

    def test_no_degradation_when_all_healthy(self):
        """Should return empty list when all configured services are available."""
        from src.runtime import validate_tier3_health

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.qdrant_url = "http://localhost:6333"
        settings.reranker_enabled = False

        svc = MagicMock()
        svc.graph_engine = MagicMock()
        svc.vector_store = MagicMock()
        svc.reranker = None
        svc.extras = {}

        degraded = validate_tier3_health(settings, svc)
        assert degraded == []

    def test_no_degradation_when_not_configured(self):
        """Should not flag services that aren't configured."""
        from src.runtime import validate_tier3_health

        settings = make_mock_settings()
        settings.neo4j_url = ""
        settings.qdrant_url = ""
        settings.reranker_enabled = False

        svc = MagicMock()
        svc.graph_engine = None
        svc.vector_store = None
        svc.reranker = None
        svc.extras = {}

        degraded = validate_tier3_health(settings, svc)
        assert degraded == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_startup_validation.py -v`
Expected: FAIL — `validate_tier3_health` not defined.

- [ ] **Step 3: Implement validate_tier3_health**

Add after the `_log_summary` function in `backend/src/runtime.py`:

```python
def validate_tier3_health(settings: Settings, svc: ServiceContainer) -> list[str]:
    """Check configured-but-missing Tier 3 services. Returns degraded names."""
    degraded: list[str] = []

    if settings.neo4j_url and not svc.graph_engine:
        logger.warning(
            "DEGRADED: Neo4j configured (JARVIS_NEO4J_URL set) but GraphEngine "
            "failed to initialize. Entity graph traversal and sync are disabled."
        )
        degraded.append("neo4j")

    if settings.qdrant_url and not svc.vector_store:
        logger.warning(
            "DEGRADED: Qdrant configured (JARVIS_QDRANT_URL set) but VectorStore "
            "failed to initialize. Semantic search and embedding are disabled."
        )
        degraded.append("qdrant")

    if getattr(settings, "reranker_enabled", False) and not svc.reranker:
        logger.warning(
            "DEGRADED: Reranker enabled but RerankerService failed to initialize."
        )
        degraded.append("reranker")

    svc.extras["degraded_services"] = degraded
    return degraded
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_startup_validation.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/runtime.py tests/test_startup_validation.py
git commit -m "feat(6c): add validate_tier3_health for startup degradation detection"
```

---

### Task 8: Final Integration + Regression Sweep

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
git commit -m "chore(6c): lint and format"
```

---

## Summary of Spec Coverage

| Spec Component | Task(s) | Status |
|---------------|---------|--------|
| 1. Elevate log levels | Task 1 | |
| 2. Health check endpoint | Tasks 4, 5 | |
| 3. Sync metrics counters | Tasks 2, 3 | |
| 4. Remove procedural memory | Task 6 | |
| 5. Startup validation | Task 7 | |
