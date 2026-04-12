# Spec 2B-i: Single Approval Gate + Hook Conversion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the triple approval gate (Governor LLM + pre-tool hook + step-level dual check) with one TrustEngine call in GraphExecutor, converting the hook to audit-only and demoting the Governor agent to edge-case fallback.

**Architecture:** The TrustEngine (built in Spec 2A) becomes the single approval gate inside `GraphExecutor._execute_step()`. It calls `get_or_assess_risk()` for a Haiku-based risk assessment, then `TrustEngine.evaluate()` for a deterministic 4×4 matrix decision. The hook stops creating approvals and becomes pure audit logging. Governor LLM agent is marked edge-case-only and only invoked when risk assessor confidence is low.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest + pytest-asyncio, ruff (line-length 100)

---

## File Map

| # | File | Action | Responsibility |
|---|------|--------|----------------|
| 1 | `src/services/graph_executor.py` | Modify | Replace dual approval check (lines 527-623) with single TrustEngine gate |
| 2 | `src/orchestrator/hooks.py` | Modify | Strip approval creation (lines 98-163), keep audit logging, always return `allowed: True` |
| 3 | `src/orchestrator/agents.py` | Modify | Add `edge_case_only` field to `SubAgent`, mark governor |
| 4 | `src/orchestrator/prompts.py` | Modify | Simplify `GOVERNOR_PROMPT` for edge-case-only role |
| 5 | `src/services/notifier.py` | Modify | Handle `auto_execute_notify` notification type |
| 6 | `src/runtime.py` | Modify | Wire TrustEngine into GraphExecutor |
| 7 | `src/orchestrator/services.py` | Modify | Add `trust_engine` field to ServiceContainer |
| 8 | `tests/test_single_approval_gate.py` | Create | TrustEngine gate integration in GraphExecutor |
| 9 | `tests/test_hooks_audit_only.py` | Create | Hook audit-only behavior |
| 10 | `tests/test_governor_demotion.py` | Create | Governor edge-case-only routing |
| 11 | `tests/test_notifier_auto_execute.py` | Create | auto_execute_notify delivery |

---

### Task 1: TrustEngine Wiring — ServiceContainer + runtime.py

**Files:**
- Modify: `src/orchestrator/services.py:46` (add trust_engine field)
- Modify: `src/runtime.py:214-223` (construct TrustEngine, pass to GraphExecutor)
- Modify: `src/services/graph_executor.py:140-171` (accept trust_engine param)
- Test: `tests/test_single_approval_gate.py`

- [ ] **Step 1: Write failing test — GraphExecutor accepts trust_engine**

```python
# tests/test_single_approval_gate.py
"""Tests for single TrustEngine approval gate in GraphExecutor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_trust_engine():
    engine = AsyncMock()
    engine.evaluate = AsyncMock()
    return engine


def _make_executor(settings, mock_db, trust_engine=None):
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        return GraphExecutor(settings, mock_db, trust_engine=trust_engine)


class TestTrustEngineWiring:
    def test_executor_accepts_trust_engine(self, settings, mock_db, mock_trust_engine):
        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        assert executor._trust_engine is mock_trust_engine

    def test_executor_works_without_trust_engine(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        assert executor._trust_engine is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_single_approval_gate.py::TestTrustEngineWiring -v`
Expected: FAIL — `GraphExecutor.__init__() got an unexpected keyword argument 'trust_engine'`

- [ ] **Step 3: Add trust_engine to ServiceContainer**

In `src/orchestrator/services.py`, add after line 46 (`graph_executor`):

```python
    trust_engine: TrustEngine | None = None
```

And add the import at the top of the file (inside the `TYPE_CHECKING` block or as a string annotation). Since the file uses `from __future__ import annotations` or string annotations, add to the imports section:

```python
from src.services.trust_engine import TrustEngine
```

If the file uses `TYPE_CHECKING`, put it there. Otherwise add a direct import.

- [ ] **Step 4: Add trust_engine param to GraphExecutor.__init__**

In `src/services/graph_executor.py`, add `trust_engine=None` to `__init__` (after `circuit_breaker`):

```python
    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        event_bus=None,
        notifier=None,
        tool_registry: ToolRegistry | None = None,
        verifier: Verifier | None = None,
        context_builder: ContextBuilder | None = None,
        connector_credentials_fn=None,
        memory_service: MemoryService | None = None,
        # Agent loop dependencies (for agentic step execution)
        db_factory=None,
        execute_tool_fn=None,
        budget=None,
        circuit_breaker=None,
        # Trust infrastructure (Spec 2B-i)
        trust_engine=None,
    ):
```

And store it:

```python
        self._trust_engine = trust_engine
```

Add after `self._circuit_breaker = circuit_breaker` (line 171).

- [ ] **Step 5: Wire TrustEngine in runtime.py**

In `src/runtime.py`, add TrustEngine construction before the GraphExecutor block (before line 214). Insert inside the existing `try` block for GraphExecutor:

```python
        trust_engine = None
        try:
            from src.services.trust_engine import TrustEngine

            trust_engine = TrustEngine(db)
            svc.trust_engine = trust_engine
        except Exception:
            logger.debug("TrustEngine unavailable for GraphExecutor", exc_info=True)
```

Then pass it to GraphExecutor:

```python
        svc.graph_executor = GraphExecutor(
            settings=settings,
            db=db,
            event_bus=event_bus,
            notifier=notifier,
            tool_registry=tool_registry,
            verifier=verifier,
            context_builder=context_builder,
            memory_service=svc.memory_service,
            trust_engine=trust_engine,
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_single_approval_gate.py::TestTrustEngineWiring -v`
Expected: PASS (both tests)

- [ ] **Step 7: Commit**

```bash
git add backend/src/orchestrator/services.py backend/src/services/graph_executor.py backend/src/runtime.py backend/tests/test_single_approval_gate.py
git commit -m "feat(spec2b-i): wire TrustEngine into GraphExecutor and ServiceContainer"
```

---

### Task 2: Single TrustEngine Gate in _execute_step()

**Files:**
- Modify: `src/services/graph_executor.py:513-623` (replace dual check with TrustEngine call)
- Test: `tests/test_single_approval_gate.py` (add gate tests)

- [ ] **Step 1: Write failing tests — three decision paths**

Append to `tests/test_single_approval_gate.py`:

```python
from src.services.risk_assessor import RiskAssessment


def _make_step(step_id="step_001", capability="email.send", status="pending"):
    step = MagicMock()
    step.step_id = step_id
    step.name = f"Step: {capability}"
    step.status = status
    step.input_data = {"capability": capability}
    step.started_at = None
    step.completed_at = None
    step.output_data = None
    step.depends_on = []
    return step


def _make_run(
    run_id="run_001",
    user_id="usr_test",
    workspace_id="ws_test",
    status="running",
):
    run = MagicMock()
    run.run_id = run_id
    run.user_id = user_id
    run.workspace_id = workspace_id
    run.status = status
    return run


class TestSingleGateApprovalRequired:
    """TrustEngine returns approval_required → step pauses, approval created."""

    @patch("src.services.graph_executor.get_or_assess_risk")
    async def test_approval_required_pauses_step(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.orchestrator.contracts import PolicyDecision

        risk = RiskAssessment(risk_level="low", reasoning="test")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="approval_required",
            justification="first_use capability",
            risk_level="low",
        )

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._create_approval_and_pause = AsyncMock()
        executor._emit_event = AsyncMock()
        executor._checkpoint = AsyncMock()

        step = _make_step()
        run = _make_run()

        await executor._execute_step(run, step)

        mock_trust_engine.evaluate.assert_called_once_with("email.send", risk)
        executor._create_approval_and_pause.assert_called_once()


class TestSingleGateAutoExecuteNotify:
    """TrustEngine returns auto_execute_notify → execute then notify."""

    @patch("src.services.graph_executor.get_or_assess_risk")
    async def test_auto_notify_executes_and_notifies(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.orchestrator.contracts import PolicyDecision

        risk = RiskAssessment(risk_level="low", reasoning="trusted capability")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="auto_execute_notify",
            justification="trusted capability",
            risk_level="low",
        )

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._run_step_action = AsyncMock(return_value={"ok": True})
        executor._notify_auto_executed = AsyncMock()
        executor._emit_event = AsyncMock()
        executor._checkpoint = AsyncMock()

        step = _make_step(status="pending")
        run = _make_run()

        await executor._execute_step(run, step)

        executor._run_step_action.assert_called_once()
        executor._notify_auto_executed.assert_called_once()


class TestSingleGateAutoExecuteSilent:
    """TrustEngine returns auto_execute_silent → execute silently."""

    @patch("src.services.graph_executor.get_or_assess_risk")
    async def test_auto_silent_executes_without_notify(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.orchestrator.contracts import PolicyDecision

        risk = RiskAssessment(risk_level="none", reasoning="no risk")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="auto_execute_silent",
            justification="autonomous + no risk",
            risk_level="none",
        )

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._run_step_action = AsyncMock(return_value={"ok": True})
        executor._notify_auto_executed = AsyncMock()
        executor._emit_event = AsyncMock()
        executor._checkpoint = AsyncMock()

        step = _make_step(status="pending")
        run = _make_run()

        await executor._execute_step(run, step)

        executor._run_step_action.assert_called_once()
        executor._notify_auto_executed.assert_not_called()


class TestSingleGateResumedStep:
    """Step already running (resumed after approval) → skip gate entirely."""

    @patch("src.services.graph_executor.get_or_assess_risk")
    async def test_resumed_step_skips_trust_check(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._run_step_action = AsyncMock(return_value={"ok": True})
        executor._emit_event = AsyncMock()
        executor._checkpoint = AsyncMock()

        step = _make_step(status="running")
        run = _make_run()

        await executor._execute_step(run, step)

        mock_risk.assert_not_called()
        mock_trust_engine.evaluate.assert_not_called()
        executor._run_step_action.assert_called_once()


class TestSingleGateFallbackNoTrustEngine:
    """No TrustEngine → fall back to old per-tool requires_approval check."""

    async def test_no_trust_engine_falls_back(self, settings, mock_db):
        executor = _make_executor(settings, mock_db, trust_engine=None)
        executor._run_step_action = AsyncMock(return_value={"ok": True})
        executor._emit_event = AsyncMock()
        executor._checkpoint = AsyncMock()

        step = _make_step(status="pending")
        run = _make_run()

        # Without trust engine and no tool_registry, should just execute
        await executor._execute_step(run, step)
        executor._run_step_action.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_single_approval_gate.py -v -k "SingleGate or Resumed or Fallback"`
Expected: FAIL — methods `_create_approval_and_pause`, `_notify_auto_executed`, `get_or_assess_risk` not found/imported

- [ ] **Step 3: Implement _execute_step replacement**

In `src/services/graph_executor.py`, add import at the top (after existing imports):

```python
from src.services.risk_assessor import RiskAssessment, get_or_assess_risk
```

Replace lines 513-625 (the entire `_execute_step` method up to `transition_step(step, "running")`) with:

```python
    async def _execute_step(self, run: TaskRun, step: TaskStep) -> None:
        """Execute a single step, with single TrustEngine approval gate.

        Decision flow:
        1. If step already running → resumed after approval, skip gate
        2. If TrustEngine available → get_or_assess_risk + evaluate
           - approval_required → pause step, create approval, notify
           - auto_execute_notify → execute, then send post-exec notification
           - auto_execute_silent → execute silently
        3. If no TrustEngine → fall back to old requires_approval flag check
        """
        already_approved = step.status == "running"

        if not already_approved:
            capability = (step.input_data or {}).get(
                "capability", (step.input_data or {}).get("task_type", "")
            )

            if self._trust_engine and capability:
                # ── Single TrustEngine gate ──────────────────────────
                risk = await self._assess_step_risk(capability, step, run)
                decision = await self._trust_engine.evaluate(capability, risk)

                if decision.decision == "approval_required":
                    await self._create_approval_and_pause(
                        run, step, capability, risk, decision
                    )
                    return

                # auto_execute_notify or auto_execute_silent — proceed
                transition_step(step, "running")
                step.started_at = step.started_at or datetime.now(timezone.utc)
                await self._db.flush()
                await self._emit_event(
                    "step.started",
                    run.user_id,
                    {"run_id": run.run_id, "step_id": step.step_id},
                    workspace_id=run.workspace_id,
                )

                resolved_input = await self._resolve_step_references(step, run.run_id)
                if resolved_input != (step.input_data or {}):
                    step.input_data = resolved_input
                    await self._db.flush()

                t0 = time.monotonic()
                try:
                    output = await self._run_step_action(step, run)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                except Exception as exc:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    await self._handle_step_failure(run, step, exc, elapsed_ms)
                    return

                if decision.decision == "auto_execute_notify":
                    await self._notify_auto_executed(run, step, risk, output)

                await self._finalize_step(run, step, output, elapsed_ms)
                return

            elif not self._trust_engine:
                # ── Fallback: old per-tool requires_approval flag ────
                needs_approval = False
                risk_level = "low"

                if self._tool_registry and capability:
                    tool = await self._tool_registry.get_tool(capability)
                    if tool and tool.requires_approval:
                        needs_approval = True
                        risk_level = tool.risk_level or "low"

                if needs_approval:
                    from src.services.approval_service import create_approval

                    approval = await create_approval(
                        self._db,
                        user_id=run.user_id,
                        workspace_id=run.workspace_id,
                        approval_type=f"step:{capability}",
                        title=f"Approve step: {step.name or capability}",
                        summary=f"Step in run {run.run_id} requires approval",
                        risk_level=risk_level,
                        execution_id=run.run_id,
                        run_id=run.run_id,
                        step_id=step.step_id,
                        requested_by=run.user_id,
                    )
                    transition_step(step, "running")
                    transition_step(step, "waiting_approval")
                    transition_run(run, "awaiting_approval")
                    await self._checkpoint(run, step.step_id, "approval_gate")
                    await self._db.flush()

                    await self._emit_event(
                        "approval_requested",
                        run.user_id,
                        {
                            "run_id": run.run_id,
                            "step_id": step.step_id,
                            "approval_id": approval.approval_id,
                            "task_type": capability,
                            "risk_level": risk_level,
                        },
                        workspace_id=run.workspace_id,
                    )

                    if self._notifier:
                        try:
                            await self._notifier.notify(
                                user_id=run.user_id,
                                notification_type="approval_request",
                                title=f"Approve: {step.name or capability}",
                                body=f"Step requires approval in run {run.run_id}",
                                data={
                                    "approval_id": approval.approval_id,
                                    "run_id": run.run_id,
                                    "step_id": step.step_id,
                                },
                                workspace_id=run.workspace_id,
                            )
                        except Exception:
                            logger.warning(
                                "Failed to notify for step approval",
                                exc_info=True,
                            )
                    return

            transition_step(step, "running")

        # ── Common execution path (resumed or no-gate-needed) ────────
        step.started_at = step.started_at or datetime.now(timezone.utc)
        await self._db.flush()
        await self._emit_event(
            "step.started",
            run.user_id,
            {"run_id": run.run_id, "step_id": step.step_id},
            workspace_id=run.workspace_id,
        )

        resolved_input = await self._resolve_step_references(step, run.run_id)
        if resolved_input != (step.input_data or {}):
            step.input_data = resolved_input
            await self._db.flush()

        t0 = time.monotonic()
        try:
            output = await self._run_step_action(step, run)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            await self._handle_step_failure(run, step, exc, elapsed_ms)
            return

        await self._finalize_step(run, step, output, elapsed_ms)
```

- [ ] **Step 4: Add helper methods to GraphExecutor**

Add these new private methods after `_execute_step`:

```python
    async def _assess_step_risk(
        self, capability: str, step: TaskStep, run: TaskRun
    ) -> RiskAssessment:
        """Call get_or_assess_risk with appropriate context. Fallback on error."""
        try:
            redis = None
            if self._settings.redis_url:
                import redis.asyncio as aioredis

                redis = aioredis.from_url(
                    self._settings.redis_url, decode_responses=True
                )

            return await get_or_assess_risk(
                capability=capability,
                step_input=step.input_data or {},
                user_context={"user_id": run.user_id},
                workspace_id=run.workspace_id or "",
                client=self._client,
                redis=redis,
            )
        except Exception:
            logger.warning(
                "Risk assessment failed for %s, defaulting to medium",
                capability,
                exc_info=True,
            )
            return RiskAssessment(
                risk_level="medium",
                reasoning="Fallback — risk assessment unavailable",
            )

    async def _create_approval_and_pause(
        self,
        run: TaskRun,
        step: TaskStep,
        capability: str,
        risk: RiskAssessment,
        decision: PolicyDecision,
    ) -> None:
        """Create approval record, pause step and run, notify user."""
        from src.services.approval_service import create_approval

        approval = await create_approval(
            self._db,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            approval_type=f"step:{capability}",
            title=f"Approve step: {step.name or capability}",
            summary=decision.justification or f"Trust gate: {risk.reasoning}",
            risk_level=risk.risk_level,
            execution_id=run.run_id,
            run_id=run.run_id,
            step_id=step.step_id,
            requested_by=run.user_id,
        )
        transition_step(step, "running")
        transition_step(step, "waiting_approval")
        transition_run(run, "awaiting_approval")
        await self._checkpoint(run, step.step_id, "approval_gate")
        await self._db.flush()

        await self._emit_event(
            "approval_requested",
            run.user_id,
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
                "approval_id": approval.approval_id,
                "capability": capability,
                "risk_level": risk.risk_level,
                "trust_decision": decision.decision,
            },
            workspace_id=run.workspace_id,
        )

        if self._notifier:
            try:
                await self._notifier.notify(
                    user_id=run.user_id,
                    notification_type="approval_request",
                    title=f"Approve: {step.name or capability}",
                    body=decision.justification or risk.reasoning,
                    data={
                        "approval_id": approval.approval_id,
                        "run_id": run.run_id,
                        "step_id": step.step_id,
                        "risk_level": risk.risk_level,
                    },
                    workspace_id=run.workspace_id,
                )
            except Exception:
                logger.warning(
                    "Failed to notify for step approval", exc_info=True
                )

    async def _notify_auto_executed(
        self,
        run: TaskRun,
        step: TaskStep,
        risk: RiskAssessment,
        output: dict | None,
    ) -> None:
        """Send post-execution notification for auto_execute_notify decisions."""
        if not self._notifier:
            return

        capability = (step.input_data or {}).get(
            "capability", (step.input_data or {}).get("task_type", "unknown")
        )
        try:
            await self._notifier.notify(
                user_id=run.user_id,
                notification_type="auto_execute_notify",
                title=f"Auto-executed: {step.name or capability}",
                body=risk.reasoning,
                data={
                    "run_id": run.run_id,
                    "step_id": step.step_id,
                    "capability": capability,
                    "risk_level": risk.risk_level,
                },
                workspace_id=run.workspace_id,
            )
        except Exception:
            logger.warning(
                "Failed to send auto_execute notification", exc_info=True
            )

    async def _finalize_step(
        self,
        run: TaskRun,
        step: TaskStep,
        output: dict | None,
        elapsed_ms: int,
    ) -> None:
        """Mark step completed, emit events, checkpoint."""
        await self._emit_event(
            "tool_call_completed",
            run.user_id,
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
                "tool_name": (step.input_data or {}).get(
                    "capability",
                    (step.input_data or {}).get("task_type", "unknown"),
                ),
                "duration_ms": elapsed_ms,
            },
            workspace_id=run.workspace_id,
        )

        transition_step(step, "completed")
        step.output_data = output
        step.completed_at = datetime.now(timezone.utc)
        await self._db.flush()

        result = StepResult(
            step_id=step.step_id,
            status="completed",
            output_data=output,
            duration_ms=elapsed_ms,
        )

        await self._checkpoint(run, step.step_id, "step_completed")

        await self._emit_event(
            "step_completed",
            run.user_id,
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
                "status": "completed",
                "duration_ms": elapsed_ms,
            },
            workspace_id=run.workspace_id,
        )
```

Also add the `PolicyDecision` import at the top of the file:

```python
from src.orchestrator.contracts import PolicyDecision
```

- [ ] **Step 5: Add _handle_step_failure if not already present**

Check if `_handle_step_failure` exists in graph_executor.py. If not, extract the failure handling from the existing code (the `except` block that follows the `_run_step_action` call around line 690+). The method should:

```python
    async def _handle_step_failure(
        self, run: TaskRun, step: TaskStep, exc: Exception, elapsed_ms: int
    ) -> None:
        """Handle step execution failure — transition, log, emit event."""
        logger.error(
            "Step %s failed after %dms: %s",
            step.step_id,
            elapsed_ms,
            exc,
            exc_info=True,
        )
        transition_step(step, "failed")
        step.output_data = {"error": str(exc)}
        step.completed_at = datetime.now(timezone.utc)
        await self._db.flush()

        await self._emit_event(
            "step_failed",
            run.user_id,
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
                "error": str(exc),
                "duration_ms": elapsed_ms,
            },
            workspace_id=run.workspace_id,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_single_approval_gate.py -v`
Expected: All tests PASS

- [ ] **Step 7: Run existing graph_executor tests to verify no regression**

Run: `cd backend && python -m pytest tests/test_graph_executor.py -v`
Expected: All existing tests still PASS

- [ ] **Step 8: Commit**

```bash
git add backend/src/services/graph_executor.py backend/tests/test_single_approval_gate.py
git commit -m "feat(spec2b-i): single TrustEngine gate in GraphExecutor._execute_step"
```

---

### Task 3: Convert governor_pre_tool_hook to Audit-Only

**Files:**
- Modify: `src/orchestrator/hooks.py:30-163` (strip approval creation, always return allowed)
- Test: `tests/test_hooks_audit_only.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hooks_audit_only.py
"""Tests for audit-only governor_pre_tool_hook (Spec 2B-i)."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_db_factory():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, db


class TestAuditOnlyHook:
    async def test_read_tool_returns_allowed(self):
        from src.orchestrator.hooks import governor_pre_tool_hook

        result = await governor_pre_tool_hook(
            "search", {}, "perceiver", user_id="usr_1", workspace_id="ws_1"
        )
        assert result["allowed"] is True

    async def test_write_tool_returns_allowed(self, mock_db_factory):
        """Write tools previously blocked — now always allowed."""
        factory, db = mock_db_factory

        # Simulate a tool with requires_approval=True in DB
        from unittest.mock import patch

        mock_tool = MagicMock()
        mock_tool.enabled = True
        mock_tool.requires_approval = True
        mock_tool.risk_level = "high"

        with patch("src.orchestrator.hooks.ToolRegistry") as MockRegistry:
            registry_instance = AsyncMock()
            registry_instance.get_tool = AsyncMock(return_value=mock_tool)
            MockRegistry.return_value = registry_instance

            from src.orchestrator.hooks import governor_pre_tool_hook

            result = await governor_pre_tool_hook(
                "gmail_send_email",
                {"to": "test@example.com", "subject": "Hi"},
                "operator",
                user_id="usr_1",
                workspace_id="ws_1",
                db_factory=factory,
            )

        assert result["allowed"] is True

    async def test_blocked_tool_still_blocked(self, mock_db_factory):
        """Blocked tools remain blocked (safety invariant)."""
        factory, db = mock_db_factory

        from unittest.mock import patch

        mock_tool = MagicMock()
        mock_tool.enabled = False
        mock_tool.requires_approval = False
        mock_tool.risk_level = "low"

        with patch("src.orchestrator.hooks.ToolRegistry") as MockRegistry:
            registry_instance = AsyncMock()
            registry_instance.get_tool = AsyncMock(return_value=mock_tool)
            MockRegistry.return_value = registry_instance

            from src.orchestrator.hooks import governor_pre_tool_hook

            result = await governor_pre_tool_hook(
                "dangerous_tool",
                {},
                "operator",
                user_id="usr_1",
                workspace_id="ws_1",
                db_factory=factory,
            )

        assert result["allowed"] is False

    async def test_no_approval_record_created(self, mock_db_factory):
        """Hook must NOT create approval records anymore."""
        factory, db = mock_db_factory

        from unittest.mock import patch

        mock_tool = MagicMock()
        mock_tool.enabled = True
        mock_tool.requires_approval = True
        mock_tool.risk_level = "medium"

        with patch("src.orchestrator.hooks.ToolRegistry") as MockRegistry:
            registry_instance = AsyncMock()
            registry_instance.get_tool = AsyncMock(return_value=mock_tool)
            MockRegistry.return_value = registry_instance

            with patch(
                "src.orchestrator.hooks.create_approval"
            ) as mock_create:
                from src.orchestrator.hooks import governor_pre_tool_hook

                await governor_pre_tool_hook(
                    "gmail_send_email",
                    {"to": "test@example.com"},
                    "operator",
                    user_id="usr_1",
                    workspace_id="ws_1",
                    db_factory=factory,
                )

                mock_create.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_hooks_audit_only.py -v`
Expected: FAIL — `test_write_tool_returns_allowed` fails (returns `allowed: False`), `test_no_approval_record_created` fails (create_approval IS called)

- [ ] **Step 3: Convert hook to audit-only**

Replace `governor_pre_tool_hook` in `src/orchestrator/hooks.py` (lines 30-166):

```python
async def governor_pre_tool_hook(
    tool_name: str,
    tool_input: dict,
    agent_name: str,
    *,
    user_id: str,
    workspace_id: str = "",
    db_factory=None,
    services: dict | None = None,
    trust_tier: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Pre-tool-use hook: audit logging only.

    Approval gating moved to TrustEngine in GraphExecutor (Spec 2B-i).
    This hook now only:
    1. Checks if the tool is blocked (disabled in registry)
    2. Logs the tool call for audit
    3. Returns allowed: True for all non-blocked tools

    Args:
        trust_tier: Trust tier of the MCP server (T0-T3).
        run_id: Current TaskRun ID (if available).

    Returns:
        {"allowed": True} for non-blocked tools
        {"allowed": False, "reason": "..."} for blocked tools
    """
    # Classify tool via registry for audit + blocked check
    is_blocked = False
    risk_level = "low"

    if db_factory:
        try:
            from src.services.tool_registry import ToolRegistry

            async with db_factory() as db:
                registry = ToolRegistry(db)
                tool_def = await registry.get_tool(tool_name)
                if tool_def:
                    is_blocked = not tool_def.enabled
                    risk_level = tool_def.risk_level
        except Exception:
            pass

    # Blocked tools never pass (safety invariant)
    if is_blocked:
        logger.warning(
            "governor_blocked_tool",
            extra={"tool": tool_name, "agent": agent_name},
        )
        return {
            "allowed": False,
            "reason": f"Tool '{tool_name}' is blocked by policy",
        }

    # Audit log — all non-blocked tools
    logger.info(
        "tool_audit",
        extra={
            "tool": tool_name,
            "agent": agent_name,
            "risk_level": risk_level,
            "workspace_id": workspace_id,
        },
    )

    return {"allowed": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_hooks_audit_only.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing tests that use hooks**

Run: `cd backend && python -m pytest tests/ -v -k "hook" --no-header`
Expected: All pass (no regressions)

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/hooks.py backend/tests/test_hooks_audit_only.py
git commit -m "feat(spec2b-i): convert governor_pre_tool_hook to audit-only"
```

---

### Task 4: Governor Agent Demotion

**Files:**
- Modify: `src/orchestrator/agents.py:186` (add `edge_case_only` to SubAgent)
- Modify: `src/orchestrator/prompts.py:484-532` (simplify GOVERNOR_PROMPT)
- Test: `tests/test_governor_demotion.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_governor_demotion.py
"""Tests for Governor agent demotion to edge-case-only (Spec 2B-i)."""

import pytest


class TestGovernorEdgeCaseOnly:
    def test_governor_marked_edge_case(self):
        from src.orchestrator.agents import AGENTS

        governor = AGENTS["governor"]
        assert governor.edge_case_only is True

    def test_other_agents_not_edge_case(self):
        from src.orchestrator.agents import AGENTS

        for name, agent in AGENTS.items():
            if name != "governor":
                assert agent.edge_case_only is False, (
                    f"{name} should not be edge_case_only"
                )

    def test_governor_prompt_simplified(self):
        from src.orchestrator.prompts import GOVERNOR_PROMPT

        # Should mention edge-case / fallback role
        assert "edge" in GOVERNOR_PROMPT.lower() or "fallback" in GOVERNOR_PROMPT.lower()
        # Should NOT contain the old "NEVER auto-approve external writes" rule
        assert "NEVER auto-approve external writes in v1" not in GOVERNOR_PROMPT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_governor_demotion.py -v`
Expected: FAIL — `SubAgent` has no `edge_case_only` attribute

- [ ] **Step 3: Add edge_case_only field to SubAgent**

In `src/orchestrator/agents.py`, add the field to the `SubAgent` dataclass (line ~196, after `thinking`):

```python
@dataclass
class SubAgent:
    """Definition of a Jarvis sub-agent."""

    name: str
    prompt: str
    model_tier: str  # opus, sonnet, haiku
    capability_scope: set[str] = field(default_factory=set)
    max_tokens: int = 4096
    temperature: float = 0.3
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    edge_case_only: bool = False
```

In `create_sub_agents()`, set `edge_case_only=True` for governor:

```python
def create_sub_agents() -> dict[str, SubAgent]:
    """Create all 7 sub-agent definitions."""
    agents = {}
    for name, prompt in AGENT_PROMPTS.items():
        agents[name] = SubAgent(
            name=name,
            prompt=prompt,
            model_tier=AGENT_MODEL_TIERS.get(name, "sonnet"),
            capability_scope=set(AGENT_CAPABILITY_SCOPES.get(name, set())),
            max_tokens=8192 if name == "planner" else 4096,
            temperature=0.1 if name == "governor" else 0.3,
            thinking=AGENT_THINKING.get(name, ThinkingConfig()),
            edge_case_only=(name == "governor"),
        )
    return agents
```

- [ ] **Step 4: Simplify GOVERNOR_PROMPT**

In `src/orchestrator/prompts.py`, replace lines 484-532:

```python
GOVERNOR_PROMPT = """\
<role>
You are the Governor agent in Jarvis — the edge-case safety fallback.

The TrustEngine handles routine approval decisions deterministically.
You are only invoked when:
1. The risk assessor confidence is LOW (< 0.7) on a novel capability
2. A capability is UNKNOWN (not in the trust matrix)
3. Multiple conflicting signals require human-level judgment

You are NOT in the normal execution path. Do not assume you see every action.
</role>

<output_format>
Report your verdict using the structured output tool:
- verdict: "auto_execute" | "approval_required" | "blocked"
- risk_level: "none" | "low" | "medium" | "high" | "critical"
- justification: why this verdict (be specific about the ambiguity)
- conditions: any conditions for approval (list of strings)
</output_format>

<rules>
1. You only see edge cases — the easy decisions are already handled
2. When uncertain, default to approval_required (not blocked)
3. Log every decision to audit trail with correlation IDs
4. Critical risk always requires approval regardless of trust level
5. Strip credentials or tokens from payloads before logging
</rules>

<examples>
Edge case: New capability "custom_webhook.send" not in trust matrix
→ verdict: approval_required, risk: medium, \
justification: "Unknown capability not yet in trust matrix — needs human review"

Edge case: Risk assessor returned low confidence (0.4) on email.send
→ verdict: approval_required, risk: medium, \
justification: "Risk assessor confidence too low to auto-decide — unusual parameters"

Edge case: Bulk operation across 50+ records
→ verdict: approval_required, risk: high, \
justification: "Bulk operation exceeds normal blast radius threshold"
</examples>
"""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_governor_demotion.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/agents.py backend/src/orchestrator/prompts.py backend/tests/test_governor_demotion.py
git commit -m "feat(spec2b-i): demote Governor agent to edge-case-only fallback"
```

---

### Task 5: Notifier auto_execute_notify Support

**Files:**
- Modify: `src/services/notifier.py:160` (add auto_execute_notify routing)
- Test: `tests/test_notifier_auto_execute.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_notifier_auto_execute.py
"""Tests for auto_execute_notify notification type (Spec 2B-i)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.notifier import Notifier


@pytest.fixture
def mock_registry():
    registry = AsyncMock()
    registry.get_active_surfaces = AsyncMock(return_value=["web", "telegram"])
    registry.get_preferred_surface = AsyncMock(return_value="web")
    return registry


@pytest.fixture
def notifier(mock_registry):
    n = Notifier(surface_registry=mock_registry)
    n._deliver = AsyncMock(return_value="sent")
    n._mark_delivered = AsyncMock()
    return n


class TestAutoExecuteNotify:
    async def test_auto_notify_sends_to_preferred_surface_only(
        self, notifier, mock_registry
    ):
        """auto_execute_notify is lower priority — preferred surface only."""
        result = await notifier.notify(
            user_id="usr_1",
            notification_type="auto_execute_notify",
            title="Auto-executed: email.send",
            body="Trusted capability, low risk",
            data={"run_id": "run_001", "step_id": "step_001"},
        )

        # Should go to preferred surface, NOT all surfaces
        notifier._deliver.assert_called_once()
        call_args = notifier._deliver.call_args
        assert call_args[0][0] == "web"  # preferred surface

    async def test_auto_notify_not_sent_to_all_surfaces(
        self, notifier, mock_registry
    ):
        """Unlike approval_request, auto_notify does NOT go to all surfaces."""
        await notifier.notify(
            user_id="usr_1",
            notification_type="auto_execute_notify",
            title="Auto-executed: calendar.create",
            body="Trusted, notifying",
            data={},
        )

        # Should be exactly 1 delivery (preferred only), not 2 (all surfaces)
        assert notifier._deliver.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_notifier_auto_execute.py -v`
Expected: The tests should actually PASS already because `auto_execute_notify` falls into the `else` branch (preferred surface only) in the existing notify() method. If they pass, great — the existing routing already handles this correctly. If they fail, proceed to step 3.

- [ ] **Step 3: If tests fail, add explicit routing**

If the `else` branch doesn't catch `auto_execute_notify` correctly, add it explicitly. In `src/services/notifier.py`, change the routing block (around line 160):

```python
        if notification_type in ("approval_request", "critical_alert"):
            # Send to ALL active surfaces
            for surface in surfaces:
                result = await self._deliver(surface, notification)
                results[surface] = result
        else:
            # auto_execute_notify, info_update, briefing — preferred surface
            preferred = await self._registry.get_preferred_surface(user_id)
            if preferred:
                result = await self._deliver(preferred, notification)
                results[preferred] = result
                await self._mark_delivered(notification.notification_id, preferred)
```

No change needed if tests pass — the existing `else` branch already handles it.

- [ ] **Step 4: Run tests to verify**

Run: `cd backend && python -m pytest tests/test_notifier_auto_execute.py -v`
Expected: All PASS

- [ ] **Step 5: Run full notifier tests for regression**

Run: `cd backend && python -m pytest tests/ -v -k "notif" --no-header`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_notifier_auto_execute.py
# Only add notifier.py if it was modified
git add backend/src/services/notifier.py 2>/dev/null || true
git commit -m "feat(spec2b-i): add auto_execute_notify notification type"
```

---

### Task 6: Integration Test — Full Gate Flow

**Files:**
- Test: `tests/test_single_approval_gate.py` (append integration tests)

- [ ] **Step 1: Write integration test — trust gate end-to-end**

Append to `tests/test_single_approval_gate.py`:

```python
class TestGateIntegrationApprovalResume:
    """Full flow: step → approval_required → pause → resume → execute."""

    @patch("src.services.graph_executor.get_or_assess_risk")
    @patch("src.services.graph_executor.create_approval")
    async def test_approval_creates_record_and_pauses(
        self, mock_create_approval, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.orchestrator.contracts import PolicyDecision

        mock_approval = MagicMock()
        mock_approval.approval_id = "apr_test_001"
        mock_create_approval.return_value = mock_approval

        risk = RiskAssessment(risk_level="medium", reasoning="external write")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="approval_required",
            justification="first_use capability",
            risk_level="medium",
        )

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._emit_event = AsyncMock()
        executor._checkpoint = AsyncMock()

        step = _make_step(capability="email.send")
        run = _make_run()

        # Patch transition functions to track calls
        with patch("src.services.graph_executor.transition_step") as mock_ts, \
             patch("src.services.graph_executor.transition_run") as mock_tr:
            await executor._execute_step(run, step)

            # Verify approval created
            mock_create_approval.assert_called_once()
            call_kwargs = mock_create_approval.call_args
            assert call_kwargs[1]["risk_level"] == "medium" if call_kwargs[1] else True

            # Verify state transitions
            assert mock_ts.call_count == 2  # running → waiting_approval
            assert mock_tr.call_count == 1  # → awaiting_approval


class TestGateIntegrationAutoNotifyFlow:
    """Full flow: step → auto_execute_notify → execute → notify."""

    @patch("src.services.graph_executor.get_or_assess_risk")
    async def test_auto_notify_full_flow(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.orchestrator.contracts import PolicyDecision

        risk = RiskAssessment(risk_level="low", reasoning="trusted calendar op")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="auto_execute_notify",
            justification="trusted + low risk",
            risk_level="low",
        )

        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value={"status": "sent"})

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._notifier = mock_notifier
        executor._run_step_action = AsyncMock(return_value={"event_id": "evt_123"})
        executor._emit_event = AsyncMock()
        executor._checkpoint = AsyncMock()
        executor._resolve_step_references = AsyncMock(
            return_value={"capability": "calendar.create"}
        )

        step = _make_step(capability="calendar.create")
        run = _make_run()

        with patch("src.services.graph_executor.transition_step"):
            await executor._execute_step(run, step)

        # Verify execution happened
        executor._run_step_action.assert_called_once()

        # Verify post-execution notification sent
        mock_notifier.notify.assert_called_once()
        notify_kwargs = mock_notifier.notify.call_args[1]
        assert notify_kwargs["notification_type"] == "auto_execute_notify"
```

- [ ] **Step 2: Run integration tests**

Run: `cd backend && python -m pytest tests/test_single_approval_gate.py -v -k "Integration"`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60`
Expected: All existing tests pass, no regressions

- [ ] **Step 4: Lint check**

Run: `cd backend && ruff check src/services/graph_executor.py src/orchestrator/hooks.py src/orchestrator/agents.py src/orchestrator/prompts.py src/services/notifier.py src/runtime.py src/orchestrator/services.py`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_single_approval_gate.py
git commit -m "test(spec2b-i): integration tests for single approval gate flow"
```

---

### Task 7: Update JARVIS_SOUL_CORE Agent Table

**Files:**
- Modify: `src/orchestrator/prompts.py:14-24` (update Governor description in agent table)

- [ ] **Step 1: Update agent table in JARVIS_SOUL_CORE**

In `src/orchestrator/prompts.py`, change the Governor row in the `<agents>` table (line ~20):

```python
| Governor   | Edge-case safety fallback (novel/ambiguous) | policy decisions       |
```

From:
```
| Governor   | Evaluate policies, gate approvals       | policy decisions       |
```

To:
```
| Governor   | Edge-case safety fallback (novel/ambiguous) | policy decisions       |
```

- [ ] **Step 2: Update rule 4 in JARVIS_SOUL_CORE**

Change rule 4 (line ~30) from:
```
4. Governor sits before every external write - policy is law, not advice
```

To:
```
4. TrustEngine gates every external write - Governor handles edge cases only
```

- [ ] **Step 3: Run related tests**

Run: `cd backend && python -m pytest tests/ -v -k "governor or prompt or soul" --no-header`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add backend/src/orchestrator/prompts.py
git commit -m "docs(spec2b-i): update JARVIS_SOUL_CORE to reflect TrustEngine gate"
```

---

## Verification Checklist

After all tasks are complete, verify:

- [ ] `cd backend && python -m pytest tests/ -v` — all tests pass
- [ ] `cd backend && ruff check src/ tests/` — no lint errors
- [ ] `cd backend && ruff format --check src/ tests/` — formatted
- [ ] TrustEngine gate works for all 3 decisions: `approval_required`, `auto_execute_notify`, `auto_execute_silent`
- [ ] Resumed steps (status=running) skip the gate entirely
- [ ] Hook never creates approval records, always returns `allowed: True` (except blocked tools)
- [ ] Governor SubAgent has `edge_case_only=True`
- [ ] No `ApprovalPolicyEngine` imports in graph_executor.py (old dual check removed)
- [ ] Fallback path still works when TrustEngine is None (graceful degradation)
