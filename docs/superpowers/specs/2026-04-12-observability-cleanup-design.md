# Spec 6C: Observability + Cleanup

**Status:** Draft
**Date:** 2026-04-12
**Dependencies:** Spec 6A (Fix Broken Pipes), Spec 6B (Add Resilience)

## Problem Statement

After Specs 6A and 6B, the pipes flow and recover from transient failures — but operators can't see what's happening. Critical failures are logged at DEBUG level (invisible at default INFO). There's no health check that distinguishes "Neo4j intentionally disabled" from "Neo4j configured but unreachable." Sync metrics don't exist. And one memory type (`procedural`) is defined but never created, creating schema confusion.

## Soul Alignment

- **"Always preserve clarity"** — operators and developers must understand system state
- **"Never fake certainty"** — a system reporting "healthy" while graph sync is silently failing fakes certainty
- **"Reduce cognitive load"** — one health endpoint beats grepping logs
- **"Choose visible, understandable behavior over black-box magic"** — observability is the antidote to black boxes

## Design

### Component 1: Elevate DLQ and Silent Failure Log Levels

**Problem:** Several critical failure paths are logged at DEBUG — invisible at the default INFO level. When these fail, data is lost or processing skips, but no one knows.

| Current log | Level | Impact |
|---|---|---|
| DLQ retry tick failures (`scheduler.py:~510`) | DEBUG | Dead letters never retried, queue grows |
| DLQ enqueue failures in worker (`worker.py:~170`) | DEBUG | Failed operations lost permanently |
| VectorStore init failure (`runtime.py:116`) | DEBUG | All embedding silently disabled |
| Follow-up notification failures (`scheduler.py`) | DEBUG | Notifications silently dropped |

**Fix:** Elevate based on data-loss impact:

```python
# scheduler.py — _tick_dlq_retry
except Exception:
    logger.warning("DLQ retry tick failed", exc_info=True)  # was: debug

# worker.py — _handle_with_retry DLQ enqueue
except Exception:
    logger.warning("DLQ enqueue failed for %s", event_id, exc_info=True)  # was: debug

# runtime.py — VectorStore init
except Exception:
    logger.warning(
        "Tier 3: VectorStore unavailable — semantic search and embedding disabled",
        exc_info=True,
    )  # was: debug with generic message

# scheduler.py — follow-up notifications
except Exception:
    logger.info("Follow-up notification re-queue failed", exc_info=True)  # was: debug
```

**Keep at DEBUG** (intentional no-ops, not failures):
- "Neo4j not configured, graph engine is no-op" — intentionally disabled
- Individual Qdrant cascade delete failures in memory expiration — already retried
- Contradiction check publish failures in extract_and_store — retried via DLQ after Spec 6B

**Files:** `scheduler.py` (3 changes), `runtime.py` (1 change), `worker.py` (1 change)

### Component 2: Health Check Endpoint for Data Stores

**Problem:** No way to distinguish between these three states for any Tier 3 service:
1. **Disabled** — env var not set, intentionally off
2. **Unreachable** — env var set, service configured, but connection fails
3. **Healthy** — connected and operational

The `/health` endpoint only reports `{"worker": {"status": "running"}}`.

**Fix:** Add `GET /v1/health/stores` that probes each data store:

**Response schema:**
```json
{
  "neo4j": {
    "status": "healthy",
    "configured": true,
    "circuit_state": "closed",
    "sync_stats": {"failures": 0, "last_error": null}
  },
  "qdrant": {
    "status": "healthy",
    "configured": true,
    "collections": 6
  },
  "postgres": {
    "status": "healthy",
    "pending_dlq": 3
  },
  "redis": {
    "status": "healthy"
  },
  "degraded_services": []
}
```

**Status logic:**
- `configured = bool(settings.neo4j_url)` / `bool(settings.qdrant_url)`
- If not configured → `status: "disabled"`
- If configured but probe fails → `status: "unreachable"`
- If configured and probe succeeds → `status: "healthy"`

**Probes:**

GraphEngine health probe:
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

VectorStore health probe:
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
```

Route handler — uses `app.state` for persistent service instances (the established pattern in this codebase, see `routes_ws.py`, `routes_events.py`, etc.):

**Prerequisite:** During `app.py` lifespan startup, store persistent instances on `app.state`:
```python
# app.py — in lifespan, after VectorStore/GraphEngine init
app.state.graph_engine = graph_engine  # may be None if not configured
app.state.vector_store = vector_store  # may be None if not configured
```

Route handler:
```python
# routes_health.py
@router.get("/v1/health/stores")
async def health_stores(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    # Use persistent instances from app.state (NOT new instances per request)
    # This ensures counters and circuit state are from the live services
    graph_engine = getattr(request.app.state, "graph_engine", None)
    vector_store = getattr(request.app.state, "vector_store", None)

    # Neo4j health — use persistent instance for accurate metrics
    if graph_engine:
        neo4j_health = await graph_engine.health()
        neo4j_health["sync_stats"] = graph_engine.get_metrics()
    elif settings.neo4j_url:
        neo4j_health = {"status": "unreachable", "configured": True,
                        "error": "GraphEngine failed to initialize at startup"}
    else:
        neo4j_health = {"status": "disabled", "configured": False}

    # Qdrant health — use persistent instance
    if vector_store:
        qdrant_health = await vector_store.health()
        qdrant_health["metrics"] = vector_store.get_metrics()
    elif settings.qdrant_url:
        qdrant_health = {"status": "unreachable", "configured": True,
                         "error": "VectorStore failed to initialize at startup"}
    else:
        qdrant_health = {"status": "disabled", "configured": False}

    # Postgres health — count pending DLQ entries
    postgres_health = {"status": "healthy"}
    try:
        from sqlalchemy import func, select
        from src.models.dead_letter import DeadLetterEntry
        result = await db.execute(
            select(func.count()).where(DeadLetterEntry.status.in_(["pending", "retrying"]))
        )
        postgres_health["pending_dlq"] = result.scalar() or 0
    except Exception:
        postgres_health = {"status": "unreachable"}

    # Redis health — use persistent instance from app.state
    redis = getattr(request.app.state, "redis", None)
    if redis:
        try:
            await redis.ping()
            redis_health = {"status": "healthy"}
        except Exception:
            redis_health = {"status": "unreachable"}
    elif settings.redis_url:
        redis_health = {"status": "unreachable", "error": "Redis failed to initialize"}
    else:
        redis_health = {"status": "disabled"}

    # Degraded services
    degraded = getattr(request.app.state, "degraded_services", [])

    return {
        "neo4j": neo4j_health,
        "qdrant": qdrant_health,
        "postgres": postgres_health,
        "redis": redis_health,
        "degraded_services": degraded,
    }
```

**Files:** `app.py` (store persistent instances on `app.state`), `graph_engine.py` (add `health()`), `vector_store.py` (add `health()`), `routes_health.py` (add endpoint)

### Component 3: Sync Metrics Counters

**Problem:** When graph sync or Qdrant upserts fail, there's no aggregate metric. You have to grep logs to count failures. `GraphSyncService.get_sync_stats()` exists (`graph_sync.py:168-173`) but is never exposed to any endpoint.

**Fix:** Add in-memory counters to `GraphEngine` and `VectorStore`. These are lightweight — no external dependency, reset on restart (acceptable for operational awareness).

GraphEngine counters:
```python
class GraphEngine:
    def __init__(self, settings):
        ...
        self._metrics = {
            "sync_success": 0,
            "sync_failure": 0,
            "last_failure_at": None,
            "last_failure_error": None,
        }

    async def sync_entity(self, ...):
        ...
        try:
            ...
            self._metrics["sync_success"] += 1
            self._circuit.record_success()
        except Exception as exc:
            self._metrics["sync_failure"] += 1
            self._metrics["last_failure_at"] = datetime.now(timezone.utc).isoformat()
            self._metrics["last_failure_error"] = str(exc)[:200]
            self._circuit.record_failure()
            ...

    def get_metrics(self) -> dict:
        return {**self._metrics, "circuit_state": self._circuit._state}
```

VectorStore counters:
```python
class VectorStore:
    def __init__(self, settings):
        ...
        self._metrics = {
            "upsert_success": 0,
            "upsert_failure": 0,
            "delete_success": 0,
            "delete_failure": 0,
        }
```

Expose via the health endpoint (Component 2):
```python
neo4j_health = await graph_engine.health()
neo4j_health["sync_stats"] = graph_engine.get_metrics()
```

**Important:** Counters must be on the **persistent** instances stored on `app.state` during lifespan startup (as described in Component 2). The health endpoint already accesses these via `request.app.state.graph_engine` — no new dependency injection mechanism needed.

**Files:** `graph_engine.py` (add counters + `get_metrics()`), `vector_store.py` (add counters + `get_metrics()`), `routes_health.py` (wire metrics into response)

### Component 4: Remove Procedural Memory Type

**Problem:** `procedural` is defined as a valid memory type but no code path creates, extracts, or retrieves procedural memories. Developers see it in the schema and assume it works. The functionality it would provide (learned workflows) is already covered by the combination of `preference` + `task_context` memories.

**Fix:** Remove `procedural` from these 3 files (confirmed by grep):

1. **`src/models/memory.py:19`** — remove from the comment listing valid types
2. **`src/api/schemas.py:11`** — remove from the `MemoryType` Literal
3. **`src/api/schemas/__init__.py:11`** — remove from the `MemoryType` Literal (this is the re-export)

`procedural` does NOT appear in `memory_service.py` or `MEMORY_EXTRACTION_PROMPT` — no changes needed there.

**Migration:** Add an Alembic migration to reclassify any existing rows (unlikely to exist, but defensive):
```python
def upgrade():
    # Reclassify any procedural memories as task_context (closest equivalent)
    op.execute(
        "UPDATE memories SET memory_type = 'task_context' WHERE memory_type = 'procedural'"
    )
```

**Files:** `src/models/memory.py`, `src/api/schemas.py`, `src/api/schemas/__init__.py`, new Alembic migration

### Component 5: Startup Validation for Tier 3 Services

**Problem:** When a Tier 3 service is configured (env var set) but fails to initialize, the system runs degraded without any clear signal. The `_log_summary` in `runtime.py:265` lists missing services at INFO level, but doesn't distinguish between "not configured" and "configured but failed."

**Fix:** Add `validate_tier3_health()` after `build()` in `runtime.py`:

```python
def validate_tier3_health(settings: Settings, svc: ServiceContainer) -> list[str]:
    """Check configured-but-missing Tier 3 services. Returns list of degraded service names."""
    degraded = []

    if settings.neo4j_url and not svc.graph_engine:
        logger.warning(
            "DEGRADED: Neo4j configured (JARVIS_NEO4J_URL=%s) but GraphEngine failed to initialize. "
            "Entity graph traversal and sync are disabled.",
            settings.neo4j_url[:30] + "...",
        )
        degraded.append("neo4j")

    if settings.qdrant_url and not svc.vector_store:
        logger.warning(
            "DEGRADED: Qdrant configured (JARVIS_QDRANT_URL=%s) but VectorStore failed to initialize. "
            "Semantic search and embedding are disabled.",
            settings.qdrant_url[:30] + "...",
        )
        degraded.append("qdrant")

    if settings.reranker_enabled and not svc.reranker:
        logger.warning(
            "DEGRADED: Reranker enabled but RerankerService failed to initialize."
        )
        degraded.append("reranker")

    svc.extras["degraded_services"] = degraded
    return degraded
```

Call after `build()`:
```python
svc = build(settings, db)
degraded = validate_tier3_health(settings, svc)
if degraded:
    logger.warning("System running with %d degraded services: %s", len(degraded), degraded)
```

The `degraded_services` list is then exposed in the health endpoint (Component 2).

**Files:** `runtime.py`

## Files Changed

| File | Action | Components |
|------|--------|-----------|
| `src/services/scheduler.py` | Modify | 1 — elevate 3 log levels |
| `src/runtime.py` | Modify | 1, 5 — elevate VectorStore log, add validate_tier3_health |
| `src/services/worker.py` | Modify | 1 — elevate DLQ enqueue log |
| `src/services/graph_engine.py` | Modify | 2, 3 — add health(), add metrics counters |
| `src/services/vector_store.py` | Modify | 2, 3 — add health(), add metrics counters |
| `src/api/routes_health.py` | Modify | 2, 3 — add /v1/health/stores endpoint |
| `src/api/app.py` | Modify | 2 — store persistent GraphEngine/VectorStore on app.state |
| `src/models/memory.py` | Modify | 4 — remove procedural from type comment |
| `src/api/schemas.py` | Modify | 4 — remove procedural from MemoryType Literal |
| `src/api/schemas/__init__.py` | Modify | 4 — remove procedural from MemoryType Literal |
| `alembic/versions/xxx_remove_procedural.py` | Create | 4 — migration to reclassify existing rows |
| `tests/test_health_stores.py` | Create | 2, 3 — health endpoint tests |
| `tests/test_neo4j_metrics.py` | Create | 3 — sync metrics counter tests |
| `tests/test_startup_validation.py` | Create | 5 — tier 3 validation tests |

## Testing Strategy

- Unit: DLQ retry failure logged at WARNING (not DEBUG)
- Unit: VectorStore unavailable logged at WARNING with explicit message
- Unit: health endpoint returns "disabled" when env var not set
- Unit: health endpoint returns "unreachable" when configured but probe fails
- Unit: health endpoint returns "healthy" with collection count when connected
- Unit: sync_entity increments success/failure counters
- Unit: get_metrics returns correct counts and circuit state
- Unit: validate_tier3_health detects configured-but-missing services
- Unit: procedural removed from valid memory types
- Integration: health endpoint returns full store status in one call

## Success Criteria

1. No data-loss failure path logged below WARNING level
2. `GET /v1/health/stores` returns accurate status for all 4 data stores
3. Sync metrics visible via health endpoint without log grepping
4. "Configured but unreachable" clearly distinguished from "intentionally disabled"
5. `procedural` memory type removed; any existing rows reclassified as `task_context`
6. Startup logs clearly warn when configured services fail to initialize

## Blast Radius

| File | Change | Risk |
|------|--------|------|
| `scheduler.py` | 3 log level changes | **VERY LOW** — no behavior change |
| `runtime.py` | 1 log level + validation function | **LOW** — additive |
| `graph_engine.py` | health() + counters in each method | **LOW** — additive metrics |
| `vector_store.py` | health() + counters | **LOW** — additive |
| `routes_health.py` | New endpoint | **LOW** — read-only |
| `models/memory.py` | Remove enum value | **LOW** — no code creates this type |
| `worker.py` | 1 log level change | **VERY LOW** |

### Total: ~11 files (7 modified, 1 migration, 3 new test files)
