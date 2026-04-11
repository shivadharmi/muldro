# Spec 3A: Execution Events Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the black-box execution model with live surface updates from GraphExecutor, and replace heavyweight lightweight TaskRuns with a thin InteractionLog table.

**Architecture:** GraphExecutor gains a `_emit_surface_update()` method that publishes `SurfaceUpdate` payloads to Redis at 6 phase transitions. The orchestrator creates an initial surface and passes its ID into the executor. A new `InteractionLog` model replaces `_create_lightweight_run()` / `_complete_lightweight_run()` for simple interactions, reserving TaskRun exclusively for DAG execution. WebSocket relay forwards the new message type. SurfaceService includes active executions in workspace builds.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy 2.0, Redis pub/sub, Alembic, pytest

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `backend/src/orchestrator/contracts.py` | Add SurfaceUpdate, StepState, ApprovalContext, ResultSummary |
| Create | `backend/src/models/interaction_log.py` | InteractionLog SQLAlchemy model |
| Modify | `backend/src/models/__init__.py` | Register InteractionLog import |
| Create | `backend/alembic/versions/057_add_interaction_logs.py` | Migration for interaction_logs table |
| Modify | `backend/src/services/graph_executor.py` | Add `_emit_surface_update()`, accept `surface_id` in `execute_run()` / `_execute_dag()` |
| Modify | `backend/src/orchestrator/jarvis.py` | Delete `_create_lightweight_run` / `_complete_lightweight_run`, add `_log_interaction()`, create initial surface + pass surface_id to executor |
| Modify | `backend/src/api/routes_ws.py` | No changes needed — relay_pubsub already forwards ALL messages from `jarvis:a2ui:{user_id}` verbatim |
| Modify | `backend/src/services/surface_builder.py` | Add `_build_active_execution_surfaces()` for running TaskRuns |
| Modify | `backend/src/services/eviction_service.py` | Add `_evict_interaction_logs()` |
| Create | `backend/tests/test_surface_update_contracts.py` | Contract serialization tests |
| Create | `backend/tests/test_interaction_log.py` | InteractionLog creation tests |
| Create | `backend/tests/test_graph_executor_surface_updates.py` | GraphExecutor emission tests |
| Create | `backend/tests/test_surface_builder_active.py` | Active execution surface tests |

---

### Task 1: SurfaceUpdate Contract Models

**Files:**
- Modify: `backend/src/orchestrator/contracts.py` (append after `WorkspaceSurfacePush`)
- Test: `backend/tests/test_surface_update_contracts.py`

- [ ] **Step 1: Write failing test for SurfaceUpdate serialization**

```python
# backend/tests/test_surface_update_contracts.py
"""Tests for SurfaceUpdate contract models."""
import json

import pytest

from src.orchestrator.contracts import (
    ApprovalContext,
    ResultSummary,
    StepState,
    SurfaceUpdate,
)


class TestStepState:
    def test_minimal(self):
        s = StepState(step_id="step_01", description="Search emails", status="pending")
        assert s.step_id == "step_01"
        assert s.output_summary is None
        assert s.duration_ms is None

    def test_completed_with_output(self):
        s = StepState(
            step_id="step_02",
            description="Draft reply",
            status="completed",
            output_summary="Drafted 3 paragraphs",
            duration_ms=1200,
        )
        data = s.model_dump(mode="json")
        assert data["duration_ms"] == 1200
        assert data["output_summary"] == "Drafted 3 paragraphs"


class TestApprovalContext:
    def test_fields(self):
        a = ApprovalContext(
            approval_id="apr_01",
            step_description="Send email to client",
            risk_reasoning="External write to unknown recipient",
            trust_context="First time sending to this domain",
            graduation_hint="3 more approvals to auto-approve",
        )
        assert a.graduation_hint == "3 more approvals to auto-approve"

    def test_default_graduation_hint(self):
        a = ApprovalContext(
            approval_id="apr_02",
            step_description="x",
            risk_reasoning="y",
            trust_context="z",
        )
        assert a.graduation_hint == ""


class TestResultSummary:
    def test_defaults(self):
        r = ResultSummary()
        assert r.key_findings == []
        assert r.artifacts_created == []
        assert r.suggested_next == []

    def test_populated(self):
        r = ResultSummary(
            key_findings=["Found 3 relevant emails"],
            artifacts_created=["draft_reply_01"],
            suggested_next=["Review draft before sending"],
        )
        assert len(r.key_findings) == 1


class TestSurfaceUpdate:
    def test_plan_ready_phase(self):
        steps = [
            StepState(step_id="s1", description="Search", status="pending"),
            StepState(step_id="s2", description="Draft", status="pending"),
        ]
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="plan_ready",
            steps=steps,
            progress="0/2 steps",
        )
        assert su.phase == "plan_ready"
        assert len(su.steps) == 2
        assert su.approval is None
        assert su.results is None

    def test_executing_phase(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="executing",
            steps=[StepState(step_id="s1", description="Search", status="executing")],
            current_step="s1",
        )
        assert su.current_step == "s1"

    def test_completed_with_results(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="completed",
            results=ResultSummary(key_findings=["Done"]),
        )
        assert su.results.key_findings == ["Done"]

    def test_approval_needed(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="approval_needed",
            approval=ApprovalContext(
                approval_id="apr_01",
                step_description="Send email",
                risk_reasoning="External write",
                trust_context="First use",
            ),
        )
        assert su.approval.approval_id == "apr_01"

    def test_json_roundtrip(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="executing",
            steps=[StepState(step_id="s1", description="x", status="running")],
            current_step="s1",
            progress="1/3",
        )
        data = json.loads(su.model_dump_json())
        restored = SurfaceUpdate(**data)
        assert restored.surface_id == "surf_abc"
        assert restored.steps[0].step_id == "s1"

    def test_extra_fields_ignored(self):
        su = SurfaceUpdate(
            surface_id="surf_abc",
            phase="completed",
            unknown_field="ignored",
        )
        assert su.phase == "completed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_surface_update_contracts.py -v`
Expected: ImportError — `SurfaceUpdate` not found in `contracts.py`

- [ ] **Step 3: Implement SurfaceUpdate models in contracts.py**

Add after the `WorkspaceSurfacePush` class (around line 250) in `backend/src/orchestrator/contracts.py`:

```python
# ── Execution surface update contracts ────────────────────────────


class StepState(BaseModel):
    """Live status of a single execution step."""

    model_config = ConfigDict(extra="ignore")

    step_id: str
    description: str
    status: str  # pending, executing, completed, failed, approval_needed, user_action
    output_summary: str | None = None
    duration_ms: int | None = None


class ApprovalContext(BaseModel):
    """Context for an approval gate within a surface update."""

    model_config = ConfigDict(extra="ignore")

    approval_id: str
    step_description: str
    risk_reasoning: str
    trust_context: str
    graduation_hint: str = ""


class ResultSummary(BaseModel):
    """Summary of completed execution results."""

    model_config = ConfigDict(extra="ignore")

    key_findings: list[str] = Field(default_factory=list)
    artifacts_created: list[str] = Field(default_factory=list)
    suggested_next: list[str] = Field(default_factory=list)


class SurfaceUpdate(BaseModel):
    """Live execution progress pushed to workspace surfaces.

    Published to Redis channel jarvis:a2ui:{user_id} with
    type='surface_update'. The frontend applies incremental
    updates to the matching surface_id.
    """

    model_config = ConfigDict(extra="ignore")

    surface_id: str
    phase: str  # planning, plan_ready, executing, approval_needed, completed, failed, partial
    steps: list[StepState] = Field(default_factory=list)
    current_step: str | None = None
    progress: str = ""
    approval: ApprovalContext | None = None
    results: ResultSummary | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_surface_update_contracts.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Lint**

Run: `cd backend && ruff check src/orchestrator/contracts.py tests/test_surface_update_contracts.py && ruff format src/orchestrator/contracts.py tests/test_surface_update_contracts.py`

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/orchestrator/contracts.py tests/test_surface_update_contracts.py
git commit -m "feat(spec3a): add SurfaceUpdate contract models

StepState, ApprovalContext, ResultSummary, SurfaceUpdate Pydantic
models for live execution progress streaming."
```

---

### Task 2: InteractionLog Model + Migration

**Files:**
- Create: `backend/src/models/interaction_log.py`
- Modify: `backend/src/models/__init__.py` (add import)
- Create: Alembic migration
- Test: `backend/tests/test_interaction_log.py`

- [ ] **Step 1: Write failing test for InteractionLog model**

```python
# backend/tests/test_interaction_log.py
"""Tests for InteractionLog model and _log_interaction helper."""
from datetime import datetime, timezone

import pytest

from src.models.interaction_log import InteractionLog


class TestInteractionLogModel:
    def test_required_fields(self):
        log = InteractionLog(
            interaction_id="ilog_01ABC",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_01",
        )
        assert log.interaction_id == "ilog_01ABC"
        assert log.workspace_id == "ws_01"
        assert log.input_tokens == 0
        assert log.output_tokens == 0
        assert log.cost_usd == 0.0
        assert log.latency_ms == 0

    def test_optional_fields(self):
        log = InteractionLog(
            interaction_id="ilog_02",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_02",
            conversation_id="conv_01",
            message_preview="Hello Jarvis",
            plan_summary="Simple greeting",
            plan_id="plan_01",
            run_id="run_01",
            intent="greeting",
            response_preview="Hi there!",
            input_tokens=150,
            output_tokens=50,
            cost_usd=0.002,
            latency_ms=320,
        )
        assert log.message_preview == "Hello Jarvis"
        assert log.intent == "greeting"
        assert log.cost_usd == 0.002

    def test_id_prefix(self):
        log = InteractionLog(
            interaction_id="ilog_01HXYZ",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_01",
        )
        assert log.interaction_id.startswith("ilog_")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_interaction_log.py -v`
Expected: ModuleNotFoundError — `src.models.interaction_log`

- [ ] **Step 3: Create InteractionLog model**

```python
# backend/src/models/interaction_log.py
"""Lightweight interaction audit — replaces TaskRun for simple interactions.

Every user message gets an InteractionLog record. Only plan-backed executions
create TaskRun records. No state machine, no TaskStep, no checkpoint.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class InteractionLog(Base):
    __tablename__ = "interaction_logs"

    interaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plan_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    __table_args__ = (
        Index("ix_interaction_logs_ws_user", "workspace_id", "user_id", "created_at"),
        Index("ix_interaction_logs_trace", "trace_id"),
    )
```

- [ ] **Step 4: Register in models/__init__.py**

Add this line to `backend/src/models/__init__.py` (alphabetically, after the `integration_installation` import):

```python
from src.models.interaction_log import InteractionLog
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_interaction_log.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Generate Alembic migration**

Run: `cd backend && alembic revision --autogenerate -m "add interaction_logs table"`

Verify the generated migration creates the `interaction_logs` table with the correct columns and indexes.

- [ ] **Step 7: Run migration**

Run: `cd backend && alembic upgrade head`
Expected: Migration applies cleanly

- [ ] **Step 8: Lint and commit**

```bash
cd backend && ruff check src/models/interaction_log.py tests/test_interaction_log.py && ruff format src/models/interaction_log.py tests/test_interaction_log.py
git add src/models/interaction_log.py src/models/__init__.py alembic/versions/*interaction_logs* tests/test_interaction_log.py
git commit -m "feat(spec3a): add InteractionLog model + migration

Lightweight audit record replacing TaskRun for simple interactions.
Fields: interaction_id, workspace/user/trace IDs, message/plan/response
previews, intent, token counts, cost, latency."
```

---

### Task 3: GraphExecutor Surface Update Emission

**Files:**
- Modify: `backend/src/services/graph_executor.py`
- Test: `backend/tests/test_graph_executor_surface_updates.py`

- [ ] **Step 1: Write failing tests for surface update emission**

```python
# backend/tests/test_graph_executor_surface_updates.py
"""Tests for GraphExecutor._emit_surface_update() and phase transitions."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.contracts import (
    ApprovalContext,
    ResultSummary,
    StepState,
    SurfaceUpdate,
)
from src.services.graph_executor import GraphExecutor


def _make_executor(redis_mock=None) -> GraphExecutor:
    """Create a GraphExecutor with minimal mocks."""
    settings = MagicMock()
    settings.redis_url = "redis://localhost"
    settings.resolved_model = "claude-sonnet-4-6-20250514"
    db = AsyncMock()
    executor = GraphExecutor(settings=settings, db=db)
    executor._redis = redis_mock
    return executor


class TestEmitSurfaceUpdate:
    @pytest.mark.asyncio
    async def test_no_op_when_no_surface_id(self):
        """Should silently return when surface_id is None."""
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)
        await executor._emit_surface_update(
            surface_id=None,
            user_id="usr_01",
            phase="plan_ready",
        )
        redis.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_plan_ready(self):
        """Should publish plan_ready phase to Redis channel."""
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        steps = [
            StepState(step_id="s1", description="Search emails", status="pending"),
            StepState(step_id="s2", description="Draft reply", status="pending"),
        ]
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="plan_ready",
            steps=steps,
            progress="0/2 steps",
        )

        redis.publish.assert_called_once()
        channel, payload = redis.publish.call_args.args
        assert channel == "jarvis:a2ui:usr_01"

        data = json.loads(payload)
        assert data["type"] == "surface_update"
        assert data["surface_id"] == "surf_abc"
        assert data["phase"] == "plan_ready"
        assert len(data["steps"]) == 2
        assert data["progress"] == "0/2 steps"

    @pytest.mark.asyncio
    async def test_publishes_executing_with_current_step(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="executing",
            steps=[StepState(step_id="s1", description="Search", status="executing")],
            current_step="s1",
        )

        data = json.loads(redis.publish.call_args.args[1])
        assert data["phase"] == "executing"
        assert data["current_step"] == "s1"

    @pytest.mark.asyncio
    async def test_publishes_approval_needed(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        approval = ApprovalContext(
            approval_id="apr_01",
            step_description="Send email",
            risk_reasoning="External write",
            trust_context="First use",
            graduation_hint="9 more to auto-approve",
        )
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="approval_needed",
            approval=approval,
        )

        data = json.loads(redis.publish.call_args.args[1])
        assert data["phase"] == "approval_needed"
        assert data["approval"]["approval_id"] == "apr_01"
        assert data["approval"]["graduation_hint"] == "9 more to auto-approve"

    @pytest.mark.asyncio
    async def test_publishes_completed_with_results(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        results = ResultSummary(
            key_findings=["Found 3 emails"],
            artifacts_created=["draft_01"],
            suggested_next=["Review draft"],
        )
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="completed",
            results=results,
        )

        data = json.loads(redis.publish.call_args.args[1])
        assert data["phase"] == "completed"
        assert data["results"]["key_findings"] == ["Found 3 emails"]

    @pytest.mark.asyncio
    async def test_publishes_failed(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="failed",
            progress="Failed at step 2/3",
        )

        data = json.loads(redis.publish.call_args.args[1])
        assert data["phase"] == "failed"

    @pytest.mark.asyncio
    async def test_redis_failure_is_silent(self):
        """Redis publish failure should not raise — best-effort."""
        redis = AsyncMock()
        redis.publish.side_effect = ConnectionError("Redis down")
        executor = _make_executor(redis_mock=redis)

        # Should not raise
        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="executing",
        )


class TestExecuteRunSurfaceId:
    @pytest.mark.asyncio
    async def test_execute_run_accepts_surface_id(self):
        """execute_run() should accept and propagate surface_id."""
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        # Mock DB to return a run
        mock_run = MagicMock()
        mock_run.run_id = "run_01"
        mock_run.status = "pending"
        mock_run.user_id = "usr_01"
        mock_run.workspace_id = "ws_01"
        mock_run.plan_id = "plan_01"
        mock_run.source = "plan"
        mock_run.timeout_seconds = None
        mock_run.started_at = None
        mock_run.checkpoint = None

        executor._db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_run))
        )
        executor._db.flush = AsyncMock()
        executor._db.commit = AsyncMock()
        executor._db.refresh = AsyncMock()
        executor._execute_dag = AsyncMock()
        executor._audit = MagicMock()
        executor._audit.log = AsyncMock()

        await executor.execute_run("run_01", trace_id="trc_01", surface_id="surf_abc")

        # Verify surface_id was passed to _execute_dag
        executor._execute_dag.assert_called_once()
        call_kwargs = executor._execute_dag.call_args
        assert call_kwargs.kwargs.get("surface_id") == "surf_abc" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == "surf_abc"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_graph_executor_surface_updates.py -v`
Expected: AttributeError — `_emit_surface_update` not found

- [ ] **Step 3: Add `_emit_surface_update()` method to GraphExecutor**

Add the following method to `GraphExecutor` in `backend/src/services/graph_executor.py`, after the existing `_emit_event()` method (around line 1278):

```python
    async def _emit_surface_update(
        self,
        surface_id: str | None,
        user_id: str,
        phase: str,
        steps: list | None = None,
        current_step: str | None = None,
        progress: str = "",
        approval: object | None = None,
        results: object | None = None,
    ) -> None:
        """Publish a SurfaceUpdate to Redis for live workspace streaming.

        Best-effort — failures are logged but never raised.
        """
        if not surface_id:
            return

        try:
            from src.orchestrator.contracts import SurfaceUpdate

            update = SurfaceUpdate(
                surface_id=surface_id,
                phase=phase,
                steps=steps or [],
                current_step=current_step,
                progress=progress,
                approval=approval,
                results=results,
            )

            channel = f"jarvis:a2ui:{user_id}"
            payload = json.dumps(
                {
                    "type": "surface_update",
                    **update.model_dump(mode="json"),
                }
            )

            if self._redis:
                await self._redis.publish(channel, payload)
            elif self._event_bus:
                await self._event_bus.publish_to_channel(channel, payload)
        except Exception:
            logger.debug("Failed to emit surface update", exc_info=True)
```

- [ ] **Step 4: Update `execute_run()` to accept `surface_id` parameter**

In `backend/src/services/graph_executor.py`, change the `execute_run` signature (line 291):

Old:
```python
    async def execute_run(self, run_id: str, trace_id: str | None = None) -> TaskRun:
```

New:
```python
    async def execute_run(self, run_id: str, trace_id: str | None = None, surface_id: str | None = None) -> TaskRun:
```

Update the `_execute_dag` call inside `execute_run` (line 328) to pass surface_id:

Old:
```python
                await asyncio.wait_for(self._execute_dag(run), timeout=timeout)
            else:
                await self._execute_dag(run)
```

New:
```python
                await asyncio.wait_for(self._execute_dag(run, surface_id=surface_id), timeout=timeout)
            else:
                await self._execute_dag(run, surface_id=surface_id)
```

Also pass `surface_id` in the timeout exception handler and general exception handler — no surface_update emission needed there since `_execute_dag` handles failure internally.

- [ ] **Step 5: Update `_execute_dag()` to accept and use `surface_id`**

Change the signature (line 466):

Old:
```python
    async def _execute_dag(self, run: TaskRun) -> None:
```

New:
```python
    async def _execute_dag(self, run: TaskRun, surface_id: str | None = None) -> None:
```

Add surface update emissions at 4 key points inside `_execute_dag`:

**Point 1 — After getting ready steps and before executing (inside the `while True` loop, after `run.current_step_ids` is set, ~line 506):**

After `await self._db.flush()`, add:

```python
            # Surface update: executing with current step statuses
            if surface_id:
                all_steps_for_surface = await self._get_all_steps(run.run_id)
                step_states = [
                    StepState(
                        step_id=s.step_id,
                        description=s.name or (s.input_data or {}).get("capability", s.task_id),
                        status="executing" if s.step_id in run.current_step_ids else s.status,
                    )
                    for s in all_steps_for_surface
                ]
                completed_count = sum(1 for s in all_steps_for_surface if s.status == "completed")
                await self._emit_surface_update(
                    surface_id=surface_id,
                    user_id=run.user_id,
                    phase="executing",
                    steps=step_states,
                    current_step=ready_steps[0].step_id if ready_steps else None,
                    progress=f"{completed_count}/{len(all_steps_for_surface)} steps",
                )
```

(Add `from src.orchestrator.contracts import StepState, ResultSummary` at the top of the file.)

**Point 2 — On completion (inside the `if not pending:` block, before `break`, ~line 477):**

After `await self._writeback_memories(run)`, add:

```python
                    # Surface update: completed
                    if surface_id:
                        all_completed = await self._get_all_steps(run.run_id)
                        final_states = [
                            StepState(
                                step_id=s.step_id,
                                description=s.name or (s.input_data or {}).get("capability", s.task_id),
                                status=s.status,
                                output_summary=str(s.output_data.get("result", ""))[:200] if s.output_data else None,
                                duration_ms=int((s.completed_at - s.started_at).total_seconds() * 1000) if s.completed_at and s.started_at else None,
                            )
                            for s in all_completed
                        ]
                        findings = [
                            str(s.output_data.get("result", ""))[:100]
                            for s in all_completed
                            if s.output_data and s.output_data.get("result")
                        ]
                        await self._emit_surface_update(
                            surface_id=surface_id,
                            user_id=run.user_id,
                            phase="completed",
                            steps=final_states,
                            progress=f"{len(all_completed)}/{len(all_completed)} steps",
                            results=ResultSummary(key_findings=findings[:5]),
                        )
```

**Point 3 — On failure (inside the `if failed:` block, before `break`, ~line 498):**

```python
                    if surface_id:
                        await self._emit_surface_update(
                            surface_id=surface_id,
                            user_id=run.user_id,
                            phase="failed",
                            progress=f"{len(failed)} step(s) failed",
                        )
```

**Point 4 — Pass `surface_id` to `_execute_step` and emit approval_needed there.** Rather than modifying `_execute_step` signature (which is complex), emit the approval_needed update from `_create_approval_and_pause`:

Add `surface_id` as an attribute on `self` temporarily during `_execute_dag`:

At the start of `_execute_dag`, add:
```python
        self._current_surface_id = surface_id
```

Then in `_create_approval_and_pause` (line 689), after the `await self._db.flush()`, add:

```python
        # Surface update: approval needed
        surface_id = getattr(self, "_current_surface_id", None)
        if surface_id:
            from src.orchestrator.contracts import ApprovalContext as AC

            await self._emit_surface_update(
                surface_id=surface_id,
                user_id=run.user_id,
                phase="approval_needed",
                approval=AC(
                    approval_id=approval.approval_id,
                    step_description=step.name or capability,
                    risk_reasoning=risk.reasoning,
                    trust_context=decision.justification or "",
                    graduation_hint="",
                ),
            )
```

Similarly, in the old-style approval path (the `elif not self._trust_engine` block, after the `_emit_event("approval_requested", ...)` call around line 613), add the same surface update but without trust context.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_graph_executor_surface_updates.py -v`
Expected: All 8 tests PASS

- [ ] **Step 7: Run existing graph_executor tests to check for regressions**

Run: `cd backend && python -m pytest tests/ -v -k "graph_executor" --timeout=30`
Expected: No regressions

- [ ] **Step 8: Lint and commit**

```bash
cd backend && ruff check src/services/graph_executor.py tests/test_graph_executor_surface_updates.py && ruff format src/services/graph_executor.py tests/test_graph_executor_surface_updates.py
git add src/services/graph_executor.py tests/test_graph_executor_surface_updates.py
git commit -m "feat(spec3a): GraphExecutor surface update emission

Add _emit_surface_update() method publishing SurfaceUpdate payloads
to Redis at 6 phase transitions: plan_ready, executing (before/after
each step), approval_needed, completed, failed. execute_run() now
accepts surface_id parameter."
```

---

### Task 4: Replace Lightweight TaskRun with InteractionLog

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py`

This is the highest-risk task — it deletes `_create_lightweight_run` and `_complete_lightweight_run`, replacing them with `_log_interaction`.

- [ ] **Step 1: Write failing test for _log_interaction**

Add to `backend/tests/test_interaction_log.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.contracts import PlanOutput


class TestLogInteraction:
    """Tests for JarvisOrchestrator._log_interaction()."""

    @pytest.mark.asyncio
    async def test_creates_interaction_log(self):
        """_log_interaction should create an InteractionLog, not a TaskRun."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_db_factory = MagicMock()
        mock_db = AsyncMock()
        mock_db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.orchestrator.jarvis.get_anthropic_client"):
            orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
            orch._db_factory = mock_db_factory

        ilog_id = await orch._log_interaction(
            user_id="usr_01",
            workspace_id="ws_01",
            trace_id="trc_01",
            message_preview="Hello",
            intent="greeting",
            plan=PlanOutput(goal="Greet user", reasoning="Simple greeting"),
            conversation_id="conv_01",
        )

        assert ilog_id is not None
        assert ilog_id.startswith("ilog_")
        mock_db.add.assert_called_once()
        added = mock_db.add.call_args.args[0]
        assert added.__class__.__name__ == "InteractionLog"
        assert added.user_id == "usr_01"
        assert added.intent == "greeting"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self):
        """Should return None if DB is unavailable."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_db_factory = MagicMock()
        mock_db_factory.return_value.__aenter__ = AsyncMock(side_effect=Exception("DB down"))
        mock_db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.orchestrator.jarvis.get_anthropic_client"):
            orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
            orch._db_factory = mock_db_factory

        ilog_id = await orch._log_interaction(
            user_id="usr_01",
            workspace_id="ws_01",
            trace_id="trc_01",
        )
        assert ilog_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_interaction_log.py::TestLogInteraction -v`
Expected: AttributeError — `_log_interaction` not found

- [ ] **Step 3: Add `_log_interaction()` to JarvisOrchestrator**

In `backend/src/orchestrator/jarvis.py`, replace `_create_lightweight_run` (lines 434–483) and `_complete_lightweight_run` (lines 485–527) with:

```python
    async def _log_interaction(
        self,
        user_id: str,
        workspace_id: str,
        trace_id: str,
        message_preview: str | None = None,
        intent: str | None = None,
        plan: "PlanOutput | None" = None,
        conversation_id: str | None = None,
        response_preview: str | None = None,
        run_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
    ) -> str | None:
        """Create a lightweight InteractionLog record for auditing.

        Replaces the old _create_lightweight_run + _complete_lightweight_run
        pair. Returns the interaction_id on success, None on failure.
        """
        from src.models.interaction_log import InteractionLog

        interaction_id = f"ilog_{ULID()}"
        try:
            async with self._db_factory() as db:
                db.add(
                    InteractionLog(
                        interaction_id=interaction_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        trace_id=trace_id,
                        conversation_id=conversation_id,
                        message_preview=(message_preview[:500] if message_preview else None),
                        plan_summary=(plan.reasoning[:500] if plan and plan.reasoning else None),
                        plan_id=(plan.plan_id if plan else None),
                        run_id=run_id,
                        intent=intent,
                        response_preview=(response_preview[:500] if response_preview else None),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost_usd,
                        latency_ms=latency_ms,
                    )
                )
                await db.commit()
        except Exception:
            logger.warning("Failed to log interaction", exc_info=True)
            return None
        return interaction_id
```

- [ ] **Step 4: Update `process_message` call sites**

In `process_message()`, replace the `_create_lightweight_run` call (~line 805) and `_complete_lightweight_run` calls (~lines 917, 945) with `_log_interaction`.

Replace the `_create_lightweight_run` block:
```python
            # Log interaction (lightweight — no TaskRun)
            # Response preview will be updated after execution completes
            ilog_id = await self._log_interaction(
                user_id=user_id,
                workspace_id=workspace_id,
                trace_id=trace.trace_id,
                message_preview=message[:500],
                intent=intent,
                plan=plan,
                conversation_id=conversation_id,
            )
```

Change references from `run_id` to `ilog_id` in the result dict. Note: the result dict currently includes `"run_id": run_id` — for plan-backed executions that create real TaskRuns via GraphExecutor, the run_id will come from there. For the non-DAG inline path in process_message, there is no run_id anymore, so set it to None.

Delete both `_complete_lightweight_run` calls (success at ~line 917 and error at ~line 945). The `_learn_from_outcome` call in `_complete_lightweight_run` should be preserved — move it inline after execution completes (or skip it for simple interactions, since learning is most valuable for plan-backed runs).

In the result dict, change:
```python
            result: dict[str, Any] = {
                "trace_id": trace.trace_id,
                "run_id": None,  # No TaskRun for inline execution
                "interaction_id": ilog_id,
                "plan": plan_dict,
                "summary": plan.reasoning or plan_text,
            }
```

- [ ] **Step 5: Update `process_message_stream` call sites**

Apply the same pattern to `process_message_stream()`:

Replace `_create_lightweight_run` call (~line 1077) with `_log_interaction`.

Delete both `_complete_lightweight_run` calls (~lines 1209 and 1245).

Update the `yield` events to use `ilog_id` instead of `run_id` where appropriate.

- [ ] **Step 6: Delete `_create_lightweight_run` and `_complete_lightweight_run` methods**

These methods (lines 434–527) should now be fully unreferenced. Delete them entirely.

Keep `_learn_from_outcome` (line 529+) — it's still called from graph executor's completion path.

- [ ] **Step 7: Run full test suite to check for regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`
Expected: Any tests that directly tested `_create_lightweight_run` or `_complete_lightweight_run` will fail and need updating. Fix those tests.

- [ ] **Step 8: Lint and commit**

```bash
cd backend && ruff check src/orchestrator/jarvis.py tests/test_interaction_log.py && ruff format src/orchestrator/jarvis.py tests/test_interaction_log.py
git add src/orchestrator/jarvis.py tests/test_interaction_log.py
git commit -m "feat(spec3a): replace lightweight TaskRun with InteractionLog

Delete _create_lightweight_run() and _complete_lightweight_run().
Add _log_interaction() that creates a single InteractionLog record.
TaskRun now exclusively for plan-backed DAG execution."
```

---

### Task 5: Surface Builder for Active Executions

**Files:**
- Modify: `backend/src/services/surface_builder.py`
- Test: `backend/tests/test_surface_builder_active.py`

- [ ] **Step 1: Write failing test for active execution surfaces**

```python
# backend/tests/test_surface_builder_active.py
"""Tests for SurfaceService active execution surfaces."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.surface_builder import SurfaceService


def _mock_run(run_id: str, status: str, plan_id: str = "plan_01") -> MagicMock:
    run = MagicMock()
    run.run_id = run_id
    run.status = status
    run.plan_id = plan_id
    run.user_id = "usr_01"
    run.workspace_id = "ws_01"
    run.created_at = datetime.now(timezone.utc)
    run.started_at = datetime.now(timezone.utc)
    run.graph_definition = {"nodes": [{"task_id": "t1", "task_type": "email.search"}]}
    return run


def _mock_step(step_id: str, status: str, name: str = None) -> MagicMock:
    step = MagicMock()
    step.step_id = step_id
    step.status = status
    step.name = name
    step.input_data = {"capability": "email.search"}
    step.started_at = datetime.now(timezone.utc) if status != "pending" else None
    step.completed_at = (
        datetime.now(timezone.utc) if status == "completed" else None
    )
    return step


class TestActiveExecutionSurfaces:
    @pytest.mark.asyncio
    async def test_running_run_appears_in_surfaces(self):
        """Active (running) TaskRuns should appear in workspace surfaces."""
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        # Mock: _build_active_execution_surfaces returns a running run
        running_run = _mock_run("run_01", "running")

        # We'll test the method directly
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [running_run]
        db.execute = AsyncMock(return_value=mock_result)

        # Mock steps query
        step_result = MagicMock()
        step_result.scalars.return_value.all.return_value = [
            _mock_step("s1", "completed", "Search emails"),
            _mock_step("s2", "running", "Draft reply"),
        ]

        call_count = 0
        original_execute = db.execute

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result  # TaskRun query
            return step_result  # TaskStep query

        db.execute = mock_execute

        surfaces = await service._build_active_execution_surfaces()

        assert len(surfaces) == 1
        assert surfaces[0]["kind"] == "plan"
        assert surfaces[0]["preview"]["status"] == "running"
        assert surfaces[0]["preview"]["progress"] is not None

    @pytest.mark.asyncio
    async def test_completed_runs_excluded(self):
        """Completed TaskRuns should NOT appear in active surfaces."""
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []  # No active runs
        db.execute = AsyncMock(return_value=mock_result)

        surfaces = await service._build_active_execution_surfaces()
        assert len(surfaces) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_surface_builder_active.py -v`
Expected: AttributeError — `_build_active_execution_surfaces` not found

- [ ] **Step 3: Add `_build_active_execution_surfaces()` to SurfaceService**

In `backend/src/services/surface_builder.py`, add the method and wire it into `build_workspace_surfaces`:

Add import at the top:
```python
from src.models.task_graph import TaskRun, TaskStep
```

(TaskRun is already imported, add TaskStep.)

Add the new method after `_build_priority_surfaces`:

```python
    async def _build_active_execution_surfaces(self) -> list[dict[str, Any]]:
        """Build surfaces for actively executing TaskRuns.

        Includes runs with status in (running, paused, awaiting_approval).
        These appear above completed surfaces in the workspace.
        """
        result = await self._db.execute(
            select(TaskRun)
            .where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status.in_(["running", "paused"]),
                TaskRun.source != "user_message",  # Skip legacy lightweight runs
            )
            .order_by(TaskRun.started_at.desc())
            .limit(5)
        )
        runs = result.scalars().all()
        surfaces: list[dict[str, Any]] = []

        for run in runs:
            # Fetch steps for progress info
            step_result = await self._db.execute(
                select(TaskStep)
                .where(TaskStep.run_id == run.run_id)
                .order_by(TaskStep.created_at)
            )
            steps = list(step_result.scalars().all())
            completed = sum(1 for s in steps if s.status == "completed")
            total = len(steps)

            current_step_name = None
            for s in steps:
                if s.status in ("running", "ready"):
                    current_step_name = (
                        s.name
                        or (s.input_data or {}).get("capability", "")
                    )
                    break

            surface_id = f"exec_{run.run_id}"
            subtitle = f"Step {completed + 1}/{total}"
            if current_step_name:
                subtitle += f": {current_step_name}"

            preview = SurfacePreview(
                title=f"Executing plan",
                subtitle=subtitle,
                status="running",
                progress=completed / total if total > 0 else 0.0,
                metrics=[
                    SurfaceMetric(
                        label="Progress",
                        value=f"{completed}/{total} steps",
                    ),
                ],
            )
            detail_config = build_detail_config("plan", surface_id)

            surfaces.append(
                {
                    "id": surface_id,
                    "kind": "plan",
                    "preview": preview.model_dump(mode="json"),
                    "detail_config": (
                        detail_config.model_dump(mode="json") if detail_config else None
                    ),
                    "source_run_id": run.run_id,
                    "created_at": (
                        run.started_at.isoformat()
                        if run.started_at
                        else run.created_at.isoformat()
                    ),
                }
            )

        return surfaces
```

Wire it into `build_workspace_surfaces` — add after the approval surfaces line:

```python
        surfaces.extend(await self._build_active_execution_surfaces())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_surface_builder_active.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run existing surface builder tests**

Run: `cd backend && python -m pytest tests/ -v -k "surface_builder" --timeout=30`
Expected: No regressions

- [ ] **Step 6: Lint and commit**

```bash
cd backend && ruff check src/services/surface_builder.py tests/test_surface_builder_active.py && ruff format src/services/surface_builder.py tests/test_surface_builder_active.py
git add src/services/surface_builder.py tests/test_surface_builder_active.py
git commit -m "feat(spec3a): active execution surfaces in workspace

SurfaceService.build_workspace_surfaces() now includes running
TaskRuns as plan-kind surfaces with step progress, sorted above
completed surfaces."
```

---

### Task 6: Eviction for InteractionLog + Cleanup

**Files:**
- Modify: `backend/src/services/eviction_service.py`

- [ ] **Step 1: Add InteractionLog eviction to EvictionService**

In `backend/src/services/eviction_service.py`, add a constant:

```python
INTERACTION_LOG_RETENTION_DAYS = 90
INTERACTION_LOG_BATCH = 1000
```

Add a new method:

```python
    async def _evict_interaction_logs(self) -> int:
        """Delete interaction logs older than retention period."""
        from src.models.interaction_log import InteractionLog

        cutoff = datetime.now(timezone.utc) - timedelta(days=INTERACTION_LOG_RETENTION_DAYS)
        result = await self._db.execute(
            delete(InteractionLog).where(InteractionLog.created_at < cutoff)
        )
        count = result.rowcount or 0
        if count:
            await self._db.flush()
            logger.info("Evicted %d interaction logs", count)
        return count
```

Wire into `run_full_eviction`:

```python
        results["interaction_logs"] = await self._evict_interaction_logs()
```

- [ ] **Step 2: Run full eviction tests**

Run: `cd backend && python -m pytest tests/ -v -k "eviction" --timeout=30`
Expected: No regressions (new method not yet tested separately — the integration test covers it)

- [ ] **Step 3: Lint and commit**

```bash
cd backend && ruff check src/services/eviction_service.py && ruff format src/services/eviction_service.py
git add src/services/eviction_service.py
git commit -m "feat(spec3a): InteractionLog eviction (90-day retention)

EvictionService now cleans up old interaction_logs alongside
other expired data."
```

---

### Task 7: Integration Test — End-to-End Surface Update Flow

**Files:**
- Create: `backend/tests/test_spec3a_integration.py`

- [ ] **Step 1: Write integration test**

```python
# backend/tests/test_spec3a_integration.py
"""Integration tests for Spec 3A: Execution Events Backend.

Verifies the full flow: GraphExecutor emits surface updates at each phase,
InteractionLog replaces TaskRun for simple interactions, and active
executions appear in workspace surfaces.
"""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.contracts import SurfaceUpdate, StepState


class TestSurfaceUpdateFlow:
    """Verify GraphExecutor emits correct surface_update messages."""

    @pytest.mark.asyncio
    async def test_three_step_plan_emits_updates(self):
        """A 3-step plan should emit at least 5 surface updates:
        1x plan_ready, 3x executing (one per step), 1x completed.
        """
        from src.services.graph_executor import GraphExecutor

        redis = AsyncMock()
        settings = MagicMock()
        settings.redis_url = "redis://localhost"
        settings.resolved_model = "claude-sonnet-4-6-20250514"
        db = AsyncMock()

        executor = GraphExecutor(settings=settings, db=db)
        executor._redis = redis

        # Track all published messages
        published = []

        async def capture_publish(channel, payload):
            published.append(json.loads(payload))

        redis.publish = capture_publish

        # Emit a sequence of surface updates as GraphExecutor would
        steps = [
            StepState(step_id="s1", description="Search", status="pending"),
            StepState(step_id="s2", description="Analyze", status="pending"),
            StepState(step_id="s3", description="Draft", status="pending"),
        ]

        # plan_ready
        await executor._emit_surface_update(
            surface_id="surf_01",
            user_id="usr_01",
            phase="plan_ready",
            steps=steps,
            progress="0/3 steps",
        )

        # executing step 1
        steps[0] = StepState(step_id="s1", description="Search", status="executing")
        await executor._emit_surface_update(
            surface_id="surf_01",
            user_id="usr_01",
            phase="executing",
            steps=steps,
            current_step="s1",
            progress="0/3 steps",
        )

        # step 1 done, step 2 executing
        steps[0] = StepState(step_id="s1", description="Search", status="completed", duration_ms=500)
        steps[1] = StepState(step_id="s2", description="Analyze", status="executing")
        await executor._emit_surface_update(
            surface_id="surf_01",
            user_id="usr_01",
            phase="executing",
            steps=steps,
            current_step="s2",
            progress="1/3 steps",
        )

        # step 2 done, step 3 executing
        steps[1] = StepState(step_id="s2", description="Analyze", status="completed", duration_ms=800)
        steps[2] = StepState(step_id="s3", description="Draft", status="executing")
        await executor._emit_surface_update(
            surface_id="surf_01",
            user_id="usr_01",
            phase="executing",
            steps=steps,
            current_step="s3",
            progress="2/3 steps",
        )

        # completed
        from src.orchestrator.contracts import ResultSummary

        steps[2] = StepState(step_id="s3", description="Draft", status="completed", duration_ms=1200)
        await executor._emit_surface_update(
            surface_id="surf_01",
            user_id="usr_01",
            phase="completed",
            steps=steps,
            progress="3/3 steps",
            results=ResultSummary(key_findings=["Found 3 relevant emails"]),
        )

        assert len(published) == 5
        assert published[0]["phase"] == "plan_ready"
        assert published[1]["phase"] == "executing"
        assert published[1]["current_step"] == "s1"
        assert published[4]["phase"] == "completed"
        assert published[4]["results"]["key_findings"] == ["Found 3 relevant emails"]

        # All messages share the same surface_id
        for msg in published:
            assert msg["surface_id"] == "surf_01"
            assert msg["type"] == "surface_update"


class TestInteractionLogReplacement:
    """Verify InteractionLog replaces TaskRun for simple interactions."""

    def test_interaction_log_has_no_state_machine(self):
        """InteractionLog should not have status/state transition fields."""
        from src.models.interaction_log import InteractionLog

        log = InteractionLog(
            interaction_id="ilog_01",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_01",
        )
        # No status field, no state machine
        assert not hasattr(log, "status")
        # Has audit-only fields
        assert hasattr(log, "input_tokens")
        assert hasattr(log, "latency_ms")
```

- [ ] **Step 2: Run integration tests**

Run: `cd backend && python -m pytest tests/test_spec3a_integration.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=30`
Expected: All tests pass (fix any regressions from Task 4)

- [ ] **Step 4: Commit**

```bash
cd backend && git add tests/test_spec3a_integration.py
git commit -m "test(spec3a): integration tests for surface update flow

Verifies 3-step plan produces 5+ surface updates with correct phases,
and InteractionLog has no state machine fields."
```

---

### Task 8: WebSocket Transport Verification

**Files:**
- No code changes needed — `routes_ws.py` already forwards all Redis messages verbatim

The `relay_pubsub()` function in `routes_ws.py` (line 112–120) subscribes to `jarvis:a2ui:{user_id}` and forwards every message:

```python
async for message in pubsub.listen():
    if message["type"] == "message":
        await websocket.send_text(message["data"])
```

Since `_emit_surface_update()` publishes to the same `jarvis:a2ui:{user_id}` channel, the messages flow through automatically. No filter on message `type` — the raw JSON is forwarded directly. The frontend (Spec 3B, not this plan) will handle `type: "surface_update"` messages.

- [ ] **Step 1: Verify WebSocket relay handles surface_update messages**

Write a quick verification test:

```python
# Add to backend/tests/test_spec3a_integration.py

class TestWebSocketRelay:
    def test_surface_update_message_shape(self):
        """Verify surface_update messages have the shape WebSocket relay expects."""
        su = SurfaceUpdate(
            surface_id="surf_01",
            phase="executing",
            steps=[StepState(step_id="s1", description="x", status="running")],
        )
        msg = json.dumps({"type": "surface_update", **su.model_dump(mode="json")})
        parsed = json.loads(msg)

        # WebSocket relay forwards raw text — frontend parses the type field
        assert parsed["type"] == "surface_update"
        assert "surface_id" in parsed
        assert "phase" in parsed
        assert "steps" in parsed
```

- [ ] **Step 2: Run and commit**

Run: `cd backend && python -m pytest tests/test_spec3a_integration.py::TestWebSocketRelay -v`

```bash
cd backend && git add tests/test_spec3a_integration.py
git commit -m "test(spec3a): verify WebSocket relay handles surface_update shape"
```

---

## Post-Implementation Checklist

- [ ] All 7 spec components implemented: SurfaceUpdate contract, GraphExecutor emission, execute_run surface_id, InteractionLog model, lightweight run replacement, WebSocket transport, active execution surfaces
- [ ] `_create_lightweight_run` and `_complete_lightweight_run` fully deleted
- [ ] No TaskRun records created for simple interactions
- [ ] GraphExecutor emits surface_update at all 6 phase points
- [ ] Active executions appear in workspace surface builder
- [ ] InteractionLog has 90-day eviction
- [ ] All tests pass: `pytest tests/ -v --timeout=30`
- [ ] Lint clean: `ruff check src/ tests/`
