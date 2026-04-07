# Spec 3A: Execution Events Backend

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1B-ii (Routing Migration) — needs PlanOutput-based execution in GraphExecutor
**Builds toward:** Spec 3B (Execution Surface Frontend)

## Problem Statement

Execution is a black box — the user sees one surface before execution, then silence until a response arrives. The GraphExecutor emits events to Redis but no frontend subscribes. Every interaction creates a heavyweight TaskRun even for simple greetings.

This spec builds the **backend half** — surface update events from GraphExecutor, InteractionLog replacement, contracts, and transport. No frontend changes.

## Design

### Component 1: SurfaceUpdate Contract

New models in contracts:

```python
class SurfaceUpdate(BaseModel):
    surface_id: str
    phase: str  # planning, plan_ready, executing, approval_needed, completed, failed, partial
    steps: list[StepState] = []
    current_step: str | None = None
    progress: str = ""
    approval: ApprovalContext | None = None
    results: ResultSummary | None = None

class StepState(BaseModel):
    step_id: str
    description: str
    status: str  # pending, executing, completed, failed, approval_needed, user_action
    output_summary: str | None = None
    duration_ms: int | None = None

class ApprovalContext(BaseModel):
    approval_id: str
    step_description: str
    risk_reasoning: str
    trust_context: str
    graduation_hint: str = ""

class ResultSummary(BaseModel):
    key_findings: list[str] = []
    artifacts_created: list[str] = []
    suggested_next: list[str] = []
```

### Component 2: GraphExecutor Surface Update Emission

Add `_emit_surface_update()` method and call at each phase transition:

```python
async def _emit_surface_update(self, surface_id, user_id, phase, **kwargs):
    if not surface_id:
        return
    update = SurfaceUpdate(surface_id=surface_id, phase=phase, **kwargs)
    # Publish to Redis for WebSocket delivery
    channel = f"jarvis:a2ui:{user_id}"
    await self._redis.publish(channel, json.dumps({
        "type": "surface_update",
        "surface_id": surface_id,
        **update.model_dump(mode="json"),
    }))
    # Update persisted surface in ui_surfaces table
    await self._update_persisted_surface(surface_id, update)
```

**Emission points:**
- After `_populate_steps()` → phase `plan_ready`
- Before each step → phase `executing`, current_step set
- After each step completes → phase `executing`, updated step statuses
- On approval gate → phase `approval_needed` with ApprovalContext
- On all complete → phase `completed` with ResultSummary
- On failure → phase `failed`

### Component 3: `execute_run()` Accepts surface_id

```python
async def execute_run(self, run_id, trace_id=None, surface_id=None):
    # ... existing setup ...
    await self._execute_dag(run, surface_id=surface_id)
```

The orchestrator creates the initial surface (phase=planning) and passes the surface_id to the executor. All subsequent updates use this same ID.

### Component 4: InteractionLog Model

New lightweight audit model replacing TaskRun for simple interactions:

```python
class InteractionLog(Base):
    __tablename__ = "interaction_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    interaction_id: Mapped[str] = mapped_column(String, unique=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    trace_id: Mapped[str] = mapped_column(String, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(nullable=True)
    message_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plan_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(nullable=True)
    run_id: Mapped[str | None] = mapped_column(nullable=True)
    intent: Mapped[str | None] = mapped_column(nullable=True)
    response_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

### Component 5: Replace Lightweight TaskRun

In `jarvis.py`:
- Delete `_create_lightweight_run()` and `_complete_lightweight_run()`
- Add `_log_interaction()` that creates a single InteractionLog record
- Replace all 4-5 call sites of `_create_lightweight_run` and 3 call sites of `_complete_lightweight_run`

### Component 6: WebSocket Transport

Update `routes_ws.py` to relay `surface_update` messages from Redis to WebSocket clients (the message is already published to the right channel — the WS handler just needs to recognize the `surface_update` type and forward it).

### Component 7: Surface Builder for Active Executions

Update `SurfaceService.build_workspace_surfaces()` to include active execution surfaces (phase != completed/failed), sorted above completed surfaces.

## Files Changed

### New Files
- `src/models/interaction_log.py`
- Alembic migration for `interaction_logs` table
- `tests/test_surface_updates.py`
- `tests/test_interaction_log.py`

### Modified Files (10)
- `src/orchestrator/contracts.py` — Add SurfaceUpdate, StepState, ApprovalContext, ResultSummary
- `src/ui/contracts.py` — Add SurfaceUpdate to A2UI types
- `src/services/graph_executor.py` — Add `_emit_surface_update()`, accept surface_id in execute_run
- `src/orchestrator/jarvis.py` — Delete lightweight run methods, add `_log_interaction()`, create initial surface and pass surface_id to executor
- `src/api/routes_ws.py` — Forward `surface_update` messages
- `src/services/surface_builder.py` — Include active execution surfaces
- `src/services/event_bus.py` — Add surface_update event type
- `src/services/eviction_service.py` — Update cleanup queries for source='user_message' TaskRuns
- `src/api/routes_runs.py` — Distinguish real runs from InteractionLog
- `src/models/task_graph.py` — Document deprecation of source='user_message'

## Testing Strategy

- Unit tests: SurfaceUpdate serialization/deserialization
- Unit tests: InteractionLog creation for each interaction type
- Unit tests: GraphExecutor emits correct phase transitions
- Integration: 3-step plan → 5+ surface updates emitted
- Integration: approval gate → approval_needed phase emitted
- Integration: simple greeting → InteractionLog (not TaskRun)

## Success Criteria

1. GraphExecutor emits surface_update events at each phase
2. Surface updates published to Redis for WS delivery
3. InteractionLog replaces lightweight TaskRun for simple interactions
4. Active executions included in workspace surface build
5. All surface update contracts validated

## Blast Radius

**Medium — execution infrastructure + orchestrator methods.**

| File | Change | Risk |
|------|--------|------|
| `src/orchestrator/jarvis.py` | Delete 2 methods, add 1, update 7 call sites | **HIGH** — core orchestrator |
| `src/services/graph_executor.py` | Add emission method + surface_id param | **MEDIUM** — additive to execution |
| `src/api/routes_ws.py` | Forward new message type | **LOW** — additive |

### Total: ~18 files (10 modified, 4 new, 4 tests)
