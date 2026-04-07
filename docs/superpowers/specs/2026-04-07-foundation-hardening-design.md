# Spec 0: Foundation Hardening

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** None — this runs FIRST, before all other specs
**Prerequisite for:** Specs 1-5

## Problem Statement

A subsystem audit of the Jarvis codebase (excluding the plan/approval/execution/perception pipeline covered by Specs 1-5) found 28 issues across 12 subsystems. The pattern: features were designed architecturally but shipped at ~60% completion. The `DeadLetterService` exists but nothing writes to it. Briefing actions are buttons that do nothing. The priority scoring formula runs but its output is ignored. Memory expiration is stored but never enforced.

These issues are prerequisites for the 5-spec redesign — if the foundation has silent failures, memory leaks, and stubbed features, the redesign will build on unstable ground.

### Issue Distribution

Of the 28 issues found, **19 are independent** (fixable now in Spec 0) and **9 are dependent** on other specs (moved to their respective specs below). This spec addresses all 19 independent issues and documents where the 9 dependent ones land.

## Issues Moved to Other Specs

These issues are best fixed alongside the spec they depend on:

| # | Issue | Moved To | Rationale |
|---|-------|----------|-----------|
| 13 | MCP Bridge: No cost/rate tracking per tool call | **Spec 2** (Trust) | Trust engine needs cost attribution per capability. Fix alongside TrustEngine implementation. |
| 18 | Notifier: Priority score computed but never used | **Spec 4** (Perception) | Spec 4 adds relevance scoring. Wire relevance → priority → delivery filtering as part of proactive intelligence. |
| 5 | Notifier: No rate limiting per surface | **Spec 4** (Perception) | Spec 4 adds proactive insight push notifications. Rate limiting must be implemented alongside push-tier delivery. |
| 22 | MCP Bridge: Tool name normalization is identity mapping | **Spec 1** (Planner) | Spec 1 introduces capability-to-tool resolution. Normalization becomes moot when capabilities resolve to tools dynamically. |
| 10 | Telegram: Accesses orchestrator private attributes | **Spec 1** (Planner) | Spec 1 refactors the orchestrator significantly. Fix private attribute access during that refactor. |
| 3 | Memory: Expired memories still returned in search | **Spec 5** (Deep Context) | Spec 5 enriches Qdrant payloads and adds payload indexing. Memory expiration enforcement + Qdrant cascade cleanup belongs there. |
| 24 | Memory: Stability score has no decay | **Spec 5** (Deep Context) | Spec 5 upgrades memory payloads with richer scoring. Fix stability decay as part of that enrichment. |
| 25 | Memory: Preference strength extracted but never used in ranking | **Spec 5** (Deep Context) | Spec 5 enriches composite scoring. Wire preference strength into TriSearch scoring formula. |
| 26 | Briefing: Related items found by timestamp, not semantic linking | **Spec 5** (Deep Context) | Spec 5 adds event embeddings and conversation embeddings. Briefing evidence can use vector similarity once available. |

## Design

All remaining 19 issues are addressed below, grouped by subsystem.

### Group 1: Security Fixes

#### Fix 1.1: Enforce OAuth encryption key at startup (#7)

**Problem:** `oauth_encryption_key` defaults to empty string. When empty, OAuth tokens stored as plaintext in DB.

**Fix:** Add startup validation in `runtime.py`:

```python
# In runtime.py, during Tier 1 initialization
if settings.oauth_encryption_key == "":
    logger.error(
        "JARVIS_OAUTH_ENCRYPTION_KEY is not set. "
        "OAuth tokens will be stored in PLAINTEXT. "
        "Set this variable to a Fernet-compatible key."
    )
    # In production, this should raise RuntimeBuildError
    # For development, allow with warning
    if settings.environment == "production":
        raise RuntimeBuildError("JARVIS_OAUTH_ENCRYPTION_KEY is required in production")
```

**Files:** `src/runtime.py`, `src/config/settings.py` (add `environment` field if missing)

#### Fix 1.2: Workspace ID validation in Notifier (#19)

**Problem:** `Notifier.notify()` receives `workspace_id` but doesn't validate it against the user's actual workspace. Potential cross-workspace notification leak.

**Fix:** Add validation in `notifier.py`:

```python
async def notify(self, user_id, workspace_id, ...):
    # Validate user belongs to workspace
    if self._db:
        member = await self._db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.workspace_id == workspace_id,
            )
        )
        if not member.scalar_one_or_none():
            logger.warning("Notification blocked: user %s not in workspace %s", user_id, workspace_id)
            return
```

**Files:** `src/services/notifier.py`

#### Fix 1.3: MCP token visibility in process list (#12)

**Problem:** OAuth tokens injected into subprocess environment variables are visible in `ps aux` output.

**Fix:** Replace environment variable injection with temporary file-based token passing:

```python
async def _inject_stdio_auth(self, server_name, env):
    """Inject auth via temp file instead of env var."""
    token = await self._get_oauth_token(server_name)
    if token:
        # Write token to temp file with tight permissions
        token_path = Path(tempfile.mkdtemp()) / f"{server_name}_token"
        token_path.write_text(token)
        token_path.chmod(0o600)
        env["TOKEN_FILE"] = str(token_path)
        # MCP server reads from file instead of env var
```

**Files:** `src/connectors/mcp_bridge.py` or `src/connectors/session_pool.py`

### Group 2: Reliability Fixes

#### Fix 2.1: Wire DeadLetterService into event processor (#6)

**Problem:** `DeadLetterService` exists but nothing calls `enqueue()`. Event processor has `except: pass` blocks that silently swallow failures.

**Fix:** Replace bare `except: pass` with DLQ writes:

```python
# In event_processor.py — replace every except: pass
try:
    await MetricsService.record_event(...)
except Exception as exc:
    logger.warning("Metrics recording failed: %s", exc)
    if self._dlq:
        await self._dlq.enqueue(
            operation_type="metrics_recording",
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
            payload={"event_id": event.event_id},
        )

# Same pattern for trigger evaluation failure (line ~298)
try:
    await self._trigger_engine.evaluate(event)
except Exception as exc:
    logger.error("Trigger evaluation failed: %s", exc)
    if self._dlq:
        await self._dlq.enqueue(
            operation_type="trigger_evaluation",
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
            payload={"event_id": event.event_id, "trigger_id": trigger.trigger_id},
        )
```

**Also:** Add DLQ retry loop to scheduler (new `_tick_dlq_retry()` method):

```python
async def _tick_dlq_retry(self):
    """Retry DLQ entries that haven't exceeded max attempts."""
    pending = await self._dlq.get_pending(limit=10)
    for entry in pending:
        if entry.attempt_count >= 3:
            await self._dlq.mark_exhausted(entry.id)
            continue
        try:
            await self._replay_dlq_entry(entry)
            await self._dlq.mark_resolved(entry.id)
        except Exception:
            await self._dlq.mark_retrying(entry.id)
```

**Files:** `src/services/event_processor.py`, `src/services/scheduler.py`, `src/services/dead_letter.py` (verify `enqueue()` method exists)

#### Fix 2.2: Worker consumer name must be unique (#1)

**Problem:** Hard-coded `"worker-1"` means multiple instances process same messages instead of distributing work.

**Fix:**

```python
import socket
import os

def _get_consumer_name():
    """Generate unique consumer name from hostname + PID."""
    hostname = socket.gethostname()
    pid = os.getpid()
    return f"worker-{hostname}-{pid}"
```

**Files:** `src/services/worker.py`

#### Fix 2.3: Worker dead-letter for failed events (#2)

**Problem:** Broken events (malformed data, missing fields) retry infinitely from the Redis stream.

**Fix:** Track retry count per message. After 3 failures, acknowledge the message and write to DLQ:

```python
async def _handle_with_retry(self, handler, event, group, consumer):
    retry_key = f"worker:retry:{event.event_id}"
    attempts = await self._redis.incr(retry_key)
    await self._redis.expire(retry_key, 3600)  # 1h TTL

    if attempts > 3:
        logger.error("Event %s exhausted retries, moving to DLQ", event.event_id)
        await self._dlq.enqueue(
            operation_type=f"worker_{group}",
            error_type="max_retries_exceeded",
            error_message=f"Failed {attempts} times",
            payload={"event_id": event.event_id, "group": group},
        )
        # Acknowledge message to stop reprocessing
        await self._bus.ack(stream, group, event.message_id)
        return
    
    await handler(event)
```

**Files:** `src/services/worker.py`

#### Fix 2.4: Budget workspace_id fallback (#8)

**Problem:** Empty `workspace_id` silently falls back to in-memory counter, not multi-instance safe.

**Fix:** Make `workspace_id` required — reject empty values:

```python
async def record_usage(self, workspace_id: str, ...):
    if not workspace_id:
        raise ValueError("workspace_id is required for budget tracking")
    # ... proceed with Redis counter
```

**Files:** `src/orchestrator/budget.py`

#### Fix 2.5: MCP tool discovery failure surfacing (#11)

**Problem:** Tool discovery failures are logged but user gets confusing errors when trying to use those tools.

**Fix:** Track discovery failures and surface via health endpoint:

```python
# In mcp_bridge.py or session_pool.py
self._discovery_failures: dict[str, str] = {}  # server_name → error

async def discover_tools(self, server_name):
    try:
        tools = await self._session.list_tools()
        self._discovery_failures.pop(server_name, None)
        return tools
    except Exception as exc:
        self._discovery_failures[server_name] = str(exc)
        logger.warning("Tool discovery failed for %s: %s", server_name, exc)
        return []

def get_discovery_health(self) -> dict[str, str]:
    """Return servers with failed tool discovery."""
    return dict(self._discovery_failures)
```

**Also:** Add to system dashboard response:
```python
# In routes_system.py or routes_health.py
"mcp_discovery_failures": bridge.get_discovery_health()
```

**Files:** `src/connectors/mcp_bridge.py`, `src/api/routes_system.py`

#### Fix 2.6: Startup worker/bot health visibility (#28)

**Problem:** Worker/bot threads mask failures — main API continues exposing endpoints that can't execute.

**Fix:** Add health tracking for worker and bot threads:

```python
# In run.py
_component_health = {"api": True, "worker": None, "bot": None}

def _run_worker():
    try:
        _component_health["worker"] = True
        asyncio.run(start_worker())
    except Exception as exc:
        _component_health["worker"] = False
        logger.error("Worker thread died: %s", exc)

# Expose via health endpoint
@router.get("/v1/health")
async def health():
    return {
        "status": "ok",
        "components": _component_health,
    }
```

**Files:** `run.py`, `src/api/routes_health.py`

### Group 3: UX Fixes

#### Fix 3.1: Implement briefing lifecycle actions (#4)

**Problem:** Pin/snooze/archive buttons exist in UI but handlers are stubbed — return True without doing anything.

**Fix:** Implement actual state transitions in `BriefingReadModel`:

```python
async def pin_briefing(self, briefing_id: str, user_id: str) -> bool:
    briefing = await self._get_briefing(briefing_id)
    if not briefing:
        return False
    briefing.pinned = True
    await self._db.flush()
    return True

async def snooze_briefing(self, briefing_id: str, user_id: str, hours: int = 4) -> bool:
    briefing = await self._get_briefing(briefing_id)
    if not briefing:
        return False
    briefing.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=hours)
    await self._db.flush()
    return True

async def archive_briefing(self, briefing_id: str, user_id: str) -> bool:
    briefing = await self._get_briefing(briefing_id)
    if not briefing:
        return False
    briefing.status = "archived"
    await self._db.flush()
    return True
```

**Requires:** Add `pinned` (bool), `snoozed_until` (datetime nullable), `status` (default "active") columns to briefing model if not present. Alembic migration.

**Files:** `src/services/briefing_read_model.py`, `src/models/briefings.py`, new alembic migration

#### Fix 3.2: Briefing generation async (#17)

**Problem:** Briefing generation blocks the request handler — can timeout for complex briefings.

**Fix:** Queue generation and return 202 Accepted:

```python
@router.get("/v1/briefings/{briefing_date}")
async def get_briefing(briefing_date: str, ...):
    # Check if briefing already exists
    existing = await read_model.get_briefing(briefing_date)
    if existing:
        return existing

    # Queue generation
    run_id = await scheduler.queue_briefing_generation(briefing_date, user_id, workspace_id)
    return JSONResponse(
        status_code=202,
        content={"status": "generating", "run_id": run_id, "message": "Briefing is being generated"},
    )
```

**Files:** `src/api/routes_briefings.py`, `src/services/scheduler.py`

#### Fix 3.3: Telegram rate limiting (#9)

**Problem:** No rate limiting — every Telegram message triggers full orchestrator run.

**Fix:** Add per-user rate limiting:

```python
# In telegram.py
_user_rate = {}  # user_id → (count, window_start)
RATE_LIMIT = 10  # messages per minute

async def _handle_message(self, update, context):
    user_id = str(update.effective_user.id)
    now = time.monotonic()

    # Rate limit check
    count, window = _user_rate.get(user_id, (0, now))
    if now - window > 60:
        count, window = 0, now
    count += 1
    _user_rate[user_id] = (count, window)

    if count > RATE_LIMIT:
        await update.message.reply_text("Slow down — I can handle 10 messages per minute.")
        return

    # ... proceed with orchestrator call
```

**Files:** `src/interface/telegram.py`

### Group 4: Performance Fixes

#### Fix 4.1: Batch Neo4j sync in worker (#14)

**Problem:** Per-entity N+1 queries to Neo4j for every event.

**Fix:** Collect all entities and relationships, then batch sync:

```python
async def _handle_entity_extraction(self, event):
    entities = await self._world_model.extract_from_event(event)
    
    # Collect all for batch sync instead of per-entity
    if self._graph_sync and entities:
        try:
            await self._graph_sync.batch_sync_entities(
                entity_ids=[e.entity_id for e in entities],
                user_id=event.user_id,
            )
        except Exception:
            logger.warning("Batch Neo4j sync failed", exc_info=True)
```

**Requires:** Add `batch_sync_entities()` method to `GraphSyncService`:

```python
async def batch_sync_entities(self, entity_ids: list[str], user_id: str):
    """Sync multiple entities and their relationships in bulk."""
    for eid in entity_ids:
        await self.sync_entity_by_id(eid)
    # Batch relationship sync
    for eid in entity_ids:
        await self.sync_relationships_for_entity(eid)
```

**Files:** `src/services/worker.py`, `src/services/graph_sync.py`

#### Fix 4.2: Defer memory contradiction checks (#15)

**Problem:** N Claude calls per new memory for contradiction checking — expensive and slow.

**Fix:** Batch contradiction checks as a deferred async job:

```python
async def extract_and_store(self, ...):
    memories = await self._extract_candidates(...)
    
    for mem in memories:
        # Store immediately without contradiction check
        await self._store_memory(mem)
    
    # Queue deferred contradiction check
    if self._scheduler:
        await self._scheduler.queue_contradiction_check(
            memory_ids=[m.memory_id for m in memories],
            user_id=user_id,
            workspace_id=workspace_id,
        )
```

**Files:** `src/services/memory_service.py`, `src/services/scheduler.py`

#### Fix 4.3: MCP circuit breaker reset endpoint (#23)

**Problem:** Circuit breakers only recover through half-open probes. No manual reset.

**Fix:** Add admin endpoint:

```python
@router.post("/v1/integrations/{server_name}/reset-circuit")
async def reset_circuit_breaker(server_name: str, ...):
    """Reset circuit breaker for an MCP server."""
    bridge = get_mcp_bridge()
    bridge.reset_circuit(server_name)
    return {"status": "reset", "server": server_name}
```

**Files:** `src/api/routes_integrations.py`, `src/connectors/mcp_bridge.py`

### Group 5: Observability Fixes

#### Fix 5.1: Reconcile trace cost with budget (#20)

**Problem:** `AgentSpan.cost_usd` and `TokenUsage` records are calculated separately, never reconciled.

**Fix:** Budget tracker reads from trace spans instead of calculating independently:

```python
async def record_from_span(self, span: AgentSpan, workspace_id: str):
    """Record budget usage from a completed trace span."""
    await self.record_usage(
        workspace_id=workspace_id,
        model=span.model,
        input_tokens=span.input_tokens,
        output_tokens=span.output_tokens,
        cache_creation_tokens=span.cache_creation_input_tokens,
        cache_read_tokens=span.cache_read_input_tokens,
        thinking_tokens=span.thinking_tokens,
    )
```

Call this from `agent_loop.py` when a span completes, instead of separate cost calculation.

**Files:** `src/orchestrator/budget.py`, `src/orchestrator/agent_loop.py`

#### Fix 5.2: Worker Neo4j sync failure tracking (#14 partial)

**Problem:** Neo4j sync failures logged but not surfaced.

**Fix:** Track failed syncs in a counter and expose via health:

```python
# In graph_sync.py
_sync_failures: int = 0
_last_sync_error: str | None = None

async def sync_entity_by_id(self, entity_id):
    try:
        ...
        self._sync_failures = max(0, self._sync_failures - 1)  # Decay on success
    except Exception as exc:
        self._sync_failures += 1
        self._last_sync_error = str(exc)[:200]
        logger.warning(...)

def get_sync_health(self) -> dict:
    return {"failures": self._sync_failures, "last_error": self._last_sync_error}
```

**Files:** `src/services/graph_sync.py`, `src/api/routes_health.py`

### Group 6: Cleanup

#### Fix 6.1: Remove unused settings (#21)

**Problem:** 6+ settings defined but never referenced in code.

**Delete from `settings.py`:**
- `twilio_account_sid`, `twilio_auth_token`, `twilio_from_number`
- `whatsapp_phone_number_id`, `whatsapp_access_token`, `whatsapp_verify_token`, `whatsapp_app_secret`
- `session_secret_key`
- `observation_stale_jira_minutes`, `observation_stale_linkedin_minutes`, `observation_stale_twitter_minutes`

**Files:** `src/config/settings.py`

#### Fix 6.2: Notifier surface sync reliability (#27)

**Problem:** Surface sync between Telegram/Web/Slack doesn't confirm delivery — other surfaces can stay stale.

**Fix:** Add delivery confirmation with polling fallback:

```python
async def _sync_surfaces(self, user_id, action, payload):
    """Sync action across all surfaces with confirmation."""
    channel = f"jarvis:surface_sync:{user_id}"
    await self._redis.publish(channel, json.dumps({"action": action, **payload}))
    
    # Store sync event for polling fallback
    sync_key = f"jarvis:pending_sync:{user_id}"
    await self._redis.lpush(sync_key, json.dumps({"action": action, **payload}))
    await self._redis.expire(sync_key, 300)  # 5 min TTL
```

Frontend polls for pending syncs on reconnection.

**Files:** `src/services/notifier.py`

## Files Changed

### Modified Files
| File | Changes |
|------|---------|
| `src/config/settings.py` | Remove 11 unused settings, add `environment` field |
| `src/runtime.py` | Add OAuth key validation, component health tracking |
| `src/services/notifier.py` | Workspace validation, surface sync reliability |
| `src/services/event_processor.py` | Replace `except: pass` with DLQ writes (~3 locations) |
| `src/services/worker.py` | Unique consumer name, DLQ for failed events, batch Neo4j sync |
| `src/services/scheduler.py` | Add `_tick_dlq_retry()`, queue briefing generation, queue contradiction check |
| `src/services/briefing_read_model.py` | Implement pin/snooze/archive actions |
| `src/services/memory_service.py` | Defer contradiction checks to async |
| `src/services/graph_sync.py` | Batch sync method, sync health tracking |
| `src/orchestrator/budget.py` | Require workspace_id (no fallback), add `record_from_span()` |
| `src/orchestrator/agent_loop.py` | Use `record_from_span()` instead of separate cost calc |
| `src/connectors/mcp_bridge.py` | Discovery failure tracking, token file injection |
| `src/interface/telegram.py` | Add rate limiting (10 msg/min per user) |
| `src/api/routes_briefings.py` | Async briefing generation (202 Accepted) |
| `src/api/routes_integrations.py` | Circuit breaker reset endpoint |
| `src/api/routes_health.py` | Component health, Neo4j sync health, MCP discovery health |
| `run.py` | Worker/bot thread health tracking |

### New Files
| File | Purpose |
|------|---------|
| Alembic migration for briefing lifecycle columns | `pinned`, `snoozed_until`, `status` on briefings table |

### Database Changes
| Table | Change |
|-------|--------|
| `briefings` | Add `pinned` (bool, default false), `snoozed_until` (datetime, nullable), `status` (string, default "active") |

## Testing Strategy

- Unit test: OAuth key validation blocks startup in production mode
- Unit test: Notifier rejects cross-workspace notifications
- Unit test: DLQ enqueue on event processor failure
- Unit test: Worker consumer name is unique per instance
- Unit test: Worker DLQ after 3 retries
- Unit test: Budget rejects empty workspace_id
- Unit test: Briefing pin/snooze/archive state transitions
- Unit test: Telegram rate limiting (11th message blocked)
- Unit test: MCP discovery failure tracking
- Unit test: Circuit breaker reset endpoint
- Integration test: DLQ retry loop processes pending entries
- Integration test: Briefing async generation returns 202 then completes

## Success Criteria

1. Zero `except: pass` blocks in event processor — all failures write to DLQ
2. OAuth tokens encrypted in all environments (startup fails without key in production)
3. Worker instances distribute work (unique consumer names)
4. Briefing pin/snooze/archive actually work
5. Telegram rate limited to 10 msg/min per user
6. MCP discovery failures visible in system dashboard
7. Budget tracking accurate in multi-instance deployment
8. All 11 unused settings removed from codebase

## Blast Radius

This spec touches many files but each change is surgical — no contract or model changes that ripple.

### Tier 1: CRITICAL — Error handling and security

| File | What changes | Why |
|------|-------------|-----|
| `src/services/event_processor.py` | Replace 3+ `except: pass` blocks with DLQ writes | Silent failure elimination |
| `src/runtime.py` | Add OAuth key validation, add component health tracking | Security + observability |
| `src/services/notifier.py` | Add workspace_id validation before delivery | Security |
| `src/orchestrator/budget.py` | Reject empty workspace_id | Budget accuracy |

### Tier 2: HIGH — Reliability

| File | What changes | Why |
|------|-------------|-----|
| `src/services/worker.py` | 3 changes: unique consumer name, DLQ for failures, batch Neo4j sync | Worker reliability |
| `src/services/scheduler.py` | Add DLQ retry tick, briefing queue, contradiction queue | Background job management |
| `src/connectors/mcp_bridge.py` | Discovery failure tracking, token file injection | MCP reliability + security |

### Tier 3: MEDIUM — UX and performance

| File | What changes | Why |
|------|-------------|-----|
| `src/services/briefing_read_model.py` | Implement 3 stubbed actions | UX completeness |
| `src/services/memory_service.py` | Defer contradiction checks | Performance |
| `src/interface/telegram.py` | Rate limiting | Abuse prevention |
| `src/api/routes_briefings.py` | Async generation | Request timeout prevention |

### Tier 4: Tests

| File | What changes | Why |
|------|-------------|-----|
| `tests/test_event_processor.py` | Add DLQ write assertions | Error handling verification |
| `tests/test_worker.py` | Consumer name uniqueness, DLQ after retries | Worker reliability |
| `tests/test_budget.py` | Reject empty workspace_id | Budget accuracy |
| `tests/test_briefing.py` | Pin/snooze/archive state transitions | UX |
| `tests/test_notifier.py` | Workspace validation, rate limiting | Security |

### Tier 5: Cleanup

| File | What changes | Why |
|------|-------------|-----|
| `src/config/settings.py` | Remove 11 unused settings | Configuration hygiene |

### Safe — Not touched by this spec

All orchestrator routing (jarvis.py), contracts, prompts, agents, graph executor, approval routes, frontend components, and database models (except briefing columns) are untouched. This spec is surgical fixes only.

### Total: ~20 files affected (14 backend source, 5 tests, 1 migration)
