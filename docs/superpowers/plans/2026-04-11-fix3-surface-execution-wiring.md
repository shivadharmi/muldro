# Fix-3: Surface & Execution Wiring

**Priority:** P1 — surfaces are half-wired, defeating Spec 3A purpose
**Risk:** Medium — touches graph_executor.py and jarvis.py execution paths
**Estimated files:** ~4 (`ui/contracts.py`, `services/graph_executor.py`, `orchestrator/jarvis.py`, `models/task_graph.py`)
**Dependencies:** Fix-2 should land first (trust decisions affect surface emission)

## Overview

The A2UI live-execution surface pipeline is broken at multiple points. The `SurfaceKind` Literal in `ui/contracts.py` is missing `"proactive_insight"`, and `SurfacePreview.status` rejects `"proposal"` — causing `_push_insight_surface` to silently fail via Pydantic `ValidationError`. In `graph_executor.py`, `_current_surface_id` is stored as mutable instance state (line 472), creating a race condition when concurrent runs share a `GraphExecutor` instance. The `resume_run` path (line 414) passes `surface_id=None`, so resumed runs never emit live surface updates. The primary execution path in `_handle_create_task` (jarvis.py line 3032) calls `execute_run` without a `surface_id`, so plan executions never stream progress to the workspace. Additionally, `_publish_progress` (line 1386) creates a new Redis connection per step event instead of reusing `self._redis`, and `_handle_step_failure` emits no surface update on permanent failure.

---

## Phase 1: Fix Surface Contracts (`backend/src/ui/contracts.py`)

### Task 1.1: Add missing `SurfaceKind` value

**File:** `backend/src/ui/contracts.py:25-37`

Add `"proactive_insight"` to the `SurfaceKind` Literal. This value is actively used in:
- `jarvis.py:2065` — `_push_insight_surface` sets `kind="proactive_insight"`
- `surface_builder.py:392,411,438` — queries and builds insight surfaces
- `routes_ws.py:312` — filters insight surfaces
- `routes_insights.py:46` — insight API endpoint

```python
# Before (line 25-37)
SurfaceKind = Literal[
    "summary",
    "briefing",
    "plan",
    "checklist",
    "approval",
    "comparison",
    "alert",
    "timeline",
    "table",
    "recommendation",
    "activity",
]

# After
SurfaceKind = Literal[
    "summary",
    "briefing",
    "plan",
    "checklist",
    "approval",
    "comparison",
    "alert",
    "timeline",
    "table",
    "recommendation",
    "activity",
    "proactive_insight",
]
```

### Task 1.2: Add `"proposal"` to `SurfacePreview.status`

**File:** `backend/src/ui/contracts.py:139-148`

`_push_insight_surface` (jarvis.py:2058) sets `status="proposal"` on the `SurfacePreview`, but the Literal only allows execution-lifecycle statuses. A `ValidationError` is raised, caught silently, and the insight surface is never pushed.

```python
# Before (line 139-148)
status: (
    Literal[
        "pending",
        "running",
        "completed",
        "failed",
        "awaiting_approval",
        "cancelled",
    ]
    | None
) = None

# After
status: (
    Literal[
        "pending",
        "running",
        "completed",
        "failed",
        "awaiting_approval",
        "cancelled",
        "proposal",
    ]
    | None
) = None
```

---

## Phase 2: Fix `surface_id` Propagation (`backend/src/services/graph_executor.py`)

### Task 2.1: Remove `_current_surface_id` instance state; pass through call chain

**File:** `backend/src/services/graph_executor.py`

**Problem:** Line 472 sets `self._current_surface_id = surface_id` at the top of `_execute_dag`. Two concurrent `_execute_dag` calls on the same `GraphExecutor` instance overwrite each other's surface_id. Lines 703 and 837 read it via `getattr(self, "_current_surface_id", None)`.

**Fix:** `_execute_dag` already receives `surface_id` as a parameter (line 470). The two sites that read `self._current_surface_id` are inside `_execute_step` and `_create_approval_and_pause`, which are called from `_execute_dag`. Thread `surface_id` through:

1. **Remove** line 472: `self._current_surface_id = surface_id`
2. **Add** `surface_id: str | None = None` parameter to `_execute_step` (line 589)
3. **Pass** `surface_id` from `_execute_dag` loop at line 580: `await self._execute_step(run, step, surface_id=surface_id)`
4. **Add** `surface_id: str | None = None` parameter to `_create_approval_and_pause` (line 774)
5. **Pass** `surface_id` from `_execute_step` into `_create_approval_and_pause` at lines 606
6. **Replace** `_surf_id = getattr(self, "_current_surface_id", None)` at lines 703 and 837 with the `surface_id` parameter
7. **Pass** `surface_id` in the legacy approval branch at line 703 (inside `_execute_step`)

**Call chain after fix:**
```
_execute_dag(run, surface_id) 
  → _execute_step(run, step, surface_id)
    → _create_approval_and_pause(run, step, capability, risk, decision, surface_id)
      → _emit_surface_update(surface_id, ...)
```

### Task 2.2: Store/retrieve `surface_id` on TaskRun for resume

**File:** `backend/src/services/graph_executor.py:291-293, 374-414`

**Problem:** `resume_run` (line 414) passes `surface_id=None` to `_execute_dag`. After an approval pause, the user approves, the run resumes, but no live surface updates are emitted because the surface_id is lost.

**Fix:** Store `surface_id` in `TaskRun.checkpoint` JSONB (already exists, line 39 of `task_graph.py`) when starting execution, and retrieve it on resume.

1. In `execute_run` (line 291-305), after `transition_run(run, "running")`, store surface_id:
   ```python
   if surface_id:
       run.checkpoint = {**(run.checkpoint or {}), "surface_id": surface_id}
   ```

2. In `resume_run` (line 410-414), retrieve before calling `_execute_dag`:
   ```python
   surface_id = (run.checkpoint or {}).get("surface_id")
   await self._execute_dag(run, surface_id=surface_id)
   ```

### Task 2.3: Emit surface update on permanent step failure in `_handle_step_failure`

**File:** `backend/src/services/graph_executor.py:884-929`

**Problem:** When a step permanently fails (retry exhausted, line 907-928), no `_emit_surface_update` is called. The surface remains stuck on "executing" phase.

**Fix:** Add `surface_id: str | None = None` parameter to `_handle_step_failure` (line 884). In the permanent failure branch (after line 928, before `await self._db.flush()`), emit:

```python
if surface_id:
    all_steps = await self._get_all_steps(run.run_id)
    step_states = [
        StepState(
            step_id=s.step_id,
            description=s.name or (s.input_data or {}).get("capability", s.task_id),
            status="failed" if s.step_id == step.step_id else s.status,
        )
        for s in all_steps
    ]
    await self._emit_surface_update(
        surface_id=surface_id,
        user_id=run.user_id,
        phase="failed",
        steps=step_states,
        progress=f"Step {step.step_id} permanently failed",
    )
```

Update callers (lines 631, 743) to pass `surface_id`:
```python
await self._handle_step_failure(run, step, exc, elapsed_ms, surface_id=surface_id)
```

### Task 2.4: Populate `steps` list in failed-branch surface emission

**File:** `backend/src/services/graph_executor.py:538-544`

**Problem:** The failed-branch in `_execute_dag` (when blocked steps exist) emits `phase="failed"` but passes no `steps=` list. The frontend receives a failure signal but cannot show which steps failed.

**Fix:** Populate `steps` with step states, matching the completed-branch pattern (lines 494-514):

```python
if surface_id:
    _fail_steps = await self._get_all_steps(run.run_id)
    _fail_states = [
        StepState(
            step_id=s.step_id,
            description=(
                s.name or (s.input_data or {}).get("capability", s.task_id)
            ),
            status=s.status,
        )
        for s in _fail_steps
    ]
    await self._emit_surface_update(
        surface_id=surface_id,
        user_id=run.user_id,
        phase="failed",
        steps=_fail_states,
        progress=f"{len(failed)} step(s) failed",
    )
```

---

## Phase 3: Wire Surface to Primary Execution Path (`backend/src/orchestrator/jarvis.py`)

### Task 3.1: Generate and pass `surface_id` from `_handle_create_task`

**File:** `backend/src/orchestrator/jarvis.py:3023-3035`

**Problem:** `_handle_create_task` calls `executor.execute_run(run.run_id, trace_id=...)` at line 3032 without a `surface_id`. This is the primary plan execution path — every user-initiated plan execution misses live surface updates.

**Fix:** Before `execute_run`, generate a surface_id and push an initial surface. The `_push_workspace_surface` or `_derive_surface_kind` logic already runs earlier in the flow — the surface_id from that should be threaded down. If a surface was already pushed for this plan (via `_push_workspace_surface`), reuse its ID. Otherwise generate one:

```python
# After run = await executor.create_run(...) at line 3023
surface_id = f"surf_{ULID()}"

# Pass to execute_run at line 3032
completed_run = await executor.execute_run(
    run.run_id,
    trace_id=trace.trace_id if trace else None,
    surface_id=surface_id,
)
```

If a workspace surface was already created earlier in the message-handling flow and its `surface_id` is available (e.g., stored on the trace or plan context), pass that instead of generating a new one. This ensures the workspace card that was already pushed receives the live execution updates.

---

## Phase 4: Fix Redis Connection Leak (`backend/src/services/graph_executor.py`)

### Task 4.1: Use `self._redis` in `_publish_progress`

**File:** `backend/src/services/graph_executor.py:1385-1397`

**Problem:** Every step event creates a new `aioredis.from_url()` connection, publishes one message, then closes it. For a 10-step plan, that is 30+ transient connections (multiple events per step).

**Fix:** Use `self._redis` if available, fallback to creating a connection only when `self._redis` is None:

```python
async def _publish_progress(self, run_id: str, data: dict) -> None:
    """Publish step progress to Redis pubsub for WebSocket consumers."""
    try:
        channel = f"jarvis:run_progress:{run_id}"
        payload = json.dumps(data)

        if self._redis:
            await self._redis.publish(channel, payload)
        else:
            import redis.asyncio as aioredis

            redis = aioredis.from_url(self._settings.redis_url)
            try:
                await redis.publish(channel, payload)
            finally:
                await redis.aclose()
    except Exception:
        logger.debug("Failed to publish run progress", exc_info=True)
```

Note: `self._redis` is already set in `__init__` (line 177) but is only passed from some callsites. The `create_graph_executor` factory (line 39) does not pass `redis=`. Consider also fixing the factory to pass a Redis instance — but that is a separate enhancement, not required for this fix.

---

## Verification

- [ ] `ruff check src/ui/contracts.py src/services/graph_executor.py src/orchestrator/jarvis.py` passes
- [ ] `pytest tests/ -v -k "surface"` — existing surface tests still pass
- [ ] `pytest tests/ -v -k "graph_executor"` — existing executor tests still pass
- [ ] Manual: `SurfacePreview(status="proposal")` no longer raises `ValidationError`
- [ ] Manual: `"proactive_insight"` is accepted by any code path that validates `SurfaceKind`
- [ ] Grep for `_current_surface_id` returns zero hits after Task 2.1
- [ ] Grep for `aioredis.from_url` in `_publish_progress` returns zero hits after Task 4.1
- [ ] Unit test: concurrent `execute_run` calls on shared `GraphExecutor` do not cross-contaminate surface_id
- [ ] Unit test: `resume_run` retrieves `surface_id` from checkpoint and emits surface updates
- [ ] Unit test: permanent step failure emits `phase="failed"` surface update with populated `steps` list
- [ ] Integration test: `_handle_create_task` → `execute_run` → surface updates visible on WebSocket channel
