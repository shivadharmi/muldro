"""DAG-based execution engine with checkpoints and approval gates.

Replaces the sequential loop in Operator with a proper graph executor
that resolves dependencies, runs independent steps in parallel,
checkpoints after each step, and pauses at approval gates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.contracts import PolicyDecision, ResultSummary, StepResult, StepState
from src.errors import classify, new_correlation_id
from src.llm_utils import parse_llm_json
from src.middleware.observability import get_correlation_id
from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskCheckpoint, TaskRun, TaskStep
from src.orchestrator.agent_loop import CancellationRequested
from src.orchestrator.tracing import JarvisTrace
from src.services.audit import AuditService
from src.services.execution_state import transition_run, transition_step
from src.services.execution_surface_emitter import SurfaceEmitter
from src.services.risk_assessor import RiskAssessment, get_or_assess_risk

if TYPE_CHECKING:
    from src.services.context_builder import ContextBuilder
    from src.services.memory_service import MemoryService
    from src.services.tool_registry import ToolRegistry
    from src.services.verifier import Verifier

logger = logging.getLogger(__name__)


def _compute_retry_delay(retry_count: int) -> int:
    """Compute exponential backoff delay in seconds, capped at 30."""
    return min(2**retry_count, 30)


def _safe_error_fields(exc: BaseException) -> dict:
    """Build the client-safe error fields for run.error / step.error /
    step.output_data and any event payload that reaches a surface.

    The raw ``str(exc)`` is for logs only (and the secret-redacted trace) — it
    is NEVER placed in these fields. Returns the safe message, a stable error
    code, and a correlation id so a user can quote it to support.
    """
    code, message, _ = classify(exc)
    return {
        "message": message,
        "error_code": code,
        "correlation_id": get_correlation_id() or new_correlation_id(),
    }


def _step_to_state(s: "TaskStep", status_override: str | None = None) -> "StepState":
    """Build a StepState from a TaskStep model, forwarding all available fields."""
    status = status_override or s.status
    started_iso = s.started_at.isoformat() if s.started_at else None
    completed_iso = s.completed_at.isoformat() if s.completed_at else None
    duration = (
        int((s.completed_at - s.started_at).total_seconds() * 1000)
        if s.completed_at and s.started_at
        else None
    )
    return StepState(
        step_id=s.step_id,
        description=s.name or (s.input_data or {}).get("capability", s.task_id),
        status=status,
        output_summary=(str(s.output_data.get("result", "")) if s.output_data else None),
        duration_ms=duration,
        started_at=started_iso,
        completed_at=completed_iso,
        timeout_seconds=s.timeout_seconds,
        error=s.error,
        retry_count=s.retry_count if s.retry_count > 0 else None,
    )


async def create_graph_executor(
    settings: Settings,
    db: AsyncSession,
    workspace_id: str = "",
    db_factory=None,
    execute_tool_fn=None,
    budget=None,
    circuit_breaker=None,
) -> GraphExecutor:
    """Factory that creates a GraphExecutor with all deps consistently resolved.

    Use this instead of instantiating GraphExecutor directly so that every
    callsite (API routes, orchestrator, runtime) gets the same dep set.
    """
    from src.services.event_bus import EventBus
    from src.services.notifier import Notifier
    from src.services.tool_registry import ToolRegistry

    event_bus: EventBus | None = None
    try:
        import redis.asyncio as aioredis

        event_bus = EventBus(aioredis.from_url(settings.redis_url, decode_responses=True))
    except Exception:
        logger.debug("EventBus unavailable for GraphExecutor", exc_info=True)

    notifier: Notifier | None = None
    try:
        import redis.asyncio as aioredis

        from src.services.surface_registry import SurfaceRegistry

        notifier_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        surface_registry = SurfaceRegistry(redis=notifier_redis)
        notifier = Notifier(
            surface_registry=surface_registry,
            redis=notifier_redis,
            db=db,
        )
    except Exception:
        logger.debug("Notifier unavailable for GraphExecutor", exc_info=True)

    tool_registry: ToolRegistry | None = None
    try:
        tool_registry = ToolRegistry(db)
    except Exception:
        logger.debug("ToolRegistry unavailable for GraphExecutor", exc_info=True)

    world_model = None
    try:
        from src.services.world_model import WorldModel

        world_model = WorldModel(settings, db)
    except Exception:
        logger.debug("WorldModel unavailable for GraphExecutor", exc_info=True)

    memory_service = None
    try:
        from src.services.memory_service import MemoryService

        memory_service = MemoryService(settings=settings, db=db)
    except Exception:
        logger.debug("MemoryService unavailable for GraphExecutor", exc_info=True)

    context_builder = None
    try:
        from src.services.context_builder import ContextBuilder

        context_builder = ContextBuilder(
            world_model=world_model,
            memory_service=memory_service,
            tool_registry=tool_registry,
            db=db,
        )
    except Exception:
        logger.debug("ContextBuilder unavailable for GraphExecutor", exc_info=True)

    verifier = None
    try:
        from src.services.verifier import Verifier

        verifier = Verifier(settings, db)
    except Exception:
        logger.debug("Verifier unavailable for GraphExecutor", exc_info=True)

    trust_engine = None
    try:
        from src.services.trust_engine import TrustEngine

        trust_engine = TrustEngine(db, workspace_id)
    except Exception:
        logger.debug("TrustEngine unavailable for GraphExecutor", exc_info=True)

    redis_conn = None
    try:
        import redis.asyncio as aioredis

        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        logger.debug("Redis unavailable for GraphExecutor", exc_info=True)

    trace_store = None
    try:
        from src.services.trace_store import TraceStore

        trace_store = TraceStore(db_factory=db_factory)
    except Exception:
        logger.debug("TraceStore unavailable for GraphExecutor", exc_info=True)

    return GraphExecutor(
        settings=settings,
        db=db,
        event_bus=event_bus,
        notifier=notifier,
        tool_registry=tool_registry,
        verifier=verifier,
        context_builder=context_builder,
        memory_service=memory_service,
        db_factory=db_factory,
        execute_tool_fn=execute_tool_fn,
        budget=budget,
        circuit_breaker=circuit_breaker,
        trust_engine=trust_engine,
        redis=redis_conn,
        trace_store=trace_store,
    )


class GraphExecutor:
    """Durable graph executor with parallel steps, checkpoints, and approval gates."""

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
        redis=None,
        # Trace persistence for background runs
        trace_store=None,
    ):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._audit = AuditService(db)
        self._event_bus = event_bus
        self._notifier = notifier
        self._tool_registry = tool_registry
        self._verifier = verifier
        self._context_builder = context_builder
        self._connector_credentials_fn = connector_credentials_fn
        self._memory_service = memory_service
        self._db_factory = db_factory
        self._execute_tool_fn = execute_tool_fn
        self._budget = budget
        self._circuit_breaker = circuit_breaker
        self._trust_engine = trust_engine
        self._redis = redis
        self._trace_store = trace_store
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._active_traces: dict[str, JarvisTrace] = {}
        # Surface/event emission cluster lives in an injected collaborator
        # (SVC-P1-3); the hub forwards to it. Built from the same deps so the
        # public constructor signature is unchanged.
        self._surface_emitter = SurfaceEmitter(
            settings=settings,
            db=db,
            event_bus=event_bus,
            redis=redis,
            db_factory=db_factory,
        )

    async def create_run(
        self,
        plan_id: str,
        user_id: str,
        workspace_id: str = "",
        source: str = "plan",
    ) -> TaskRun:
        """Create a TaskRun from a Plan, building the step DAG.

        Args:
            source: Origin of the run. "plan" for user-initiated,
                    "background" for perception-generated plans queued
                    for deferred execution by the scheduler.
        """
        result = await self._db.execute(select(Plan).where(Plan.plan_id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            raise ValueError(f"Plan not found: {plan_id}")

        run_id = f"run_{ULID()}"
        run = TaskRun(
            run_id=run_id,
            plan_id=plan_id,
            user_id=user_id,
            workspace_id=workspace_id,
            source=source,
            status="pending",
        )
        self._db.add(run)
        await self._populate_steps(run, plan)
        return run

    async def populate_run_steps(self, run_id: str, plan_id: str) -> None:
        """Populate an existing TaskRun (created by Governor) with steps from a plan."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        result = await self._db.execute(select(Plan).where(Plan.plan_id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            raise ValueError(f"Plan not found: {plan_id}")

        await self._populate_steps(run, plan)

    async def _populate_steps(self, run: TaskRun, plan: Plan) -> None:
        """Build step DAG from plan tasks onto an existing run."""
        result = await self._db.execute(
            select(PlanTask).where(PlanTask.plan_id == plan.plan_id).order_by(PlanTask.id)
        )
        tasks = list(result.scalars().all())

        graph_def = self._build_graph_definition(tasks)
        run.graph_definition = graph_def

        # Pre-build context pack for this run
        if self._context_builder and tasks:
            try:
                first_type = tasks[0].task_type if tasks[0].task_type else None
                pack = await self._context_builder.build(
                    user_id=run.user_id,
                    query=plan.goal or "",
                    task_type=first_type,
                )
                from src.services.context_builder import ContextBuilder

                prompt = ContextBuilder.to_prompt(pack)
                if prompt:
                    run.context_pack_json = pack.model_dump()
            except Exception:
                logger.debug("ContextBuilder failed at run creation", exc_info=True)

        # Create steps from tasks with plan_task_id linkage
        task_id_to_step_id = {}
        for task in tasks:
            step_id = f"step_{ULID()}"
            task_id_to_step_id[task.task_id] = step_id

        for task in tasks:
            step_id = task_id_to_step_id[task.task_id]
            depends_on_step_ids = []
            if task.depends_on:
                deps = task.depends_on if isinstance(task.depends_on, list) else []
                for dep_task_id in deps:
                    if dep_task_id in task_id_to_step_id:
                        depends_on_step_ids.append(task_id_to_step_id[dep_task_id])

            step_input = dict(task.input_data) if task.input_data else {}
            if task.task_type and "task_type" not in step_input:
                step_input["task_type"] = task.task_type

            # Derive step name from available fields
            step_name = (
                step_input.get("description") or task.task_type or step_input.get("capability")
            )

            step = TaskStep(
                step_id=step_id,
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                task_id=task.task_id,
                plan_task_id=task.task_id,
                depends_on=depends_on_step_ids or None,
                status="pending",
                input_data=step_input or None,
                name=step_name,
            )
            self._db.add(step)

        await self._db.flush()
        logger.info(
            "Run %s populated with %d steps for plan %s",
            run.run_id,
            len(tasks),
            plan.plan_id,
        )

    async def execute_run(
        self, run_id: str, trace_id: str | None = None, surface_id: str | None = None
    ) -> TaskRun:
        """Execute a run's DAG to completion (or pause at approval gate).

        A unified ``run_{run_id}`` surface is always maintained for each run.
        Callers may pass ``surface_id`` to override (e.g. for a chat-linked
        surface that predates the run); otherwise the run-scoped default is
        used so that every caller — live WebSocket push, REST poll, and detail
        modal — targets the same surface.
        """
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        # Create a live JarvisTrace so agent_loop can accumulate spans.
        effective_trace_id = trace_id or f"trace_{ULID()}"
        # Always stamp run.trace_id BEFORE step execution so the detail
        # endpoint can resolve token/cost totals on a running or completed
        # run, not only on runs that happened to be passed a trace_id.
        run.trace_id = effective_trace_id
        trace = JarvisTrace(
            trace_id=effective_trace_id,
            trigger=f"execution:{run.source or 'background'}",
        )
        self._active_traces[run.run_id] = trace

        transition_run(run, "running")
        run.started_at = datetime.now(timezone.utc)

        # Default surface_id to the canonical run-scoped id so all emitters
        # (execute_run, _build_run_surfaces, detail modal) converge on the
        # same id and the frontend naturally deduplicates.
        if not surface_id:
            surface_id = f"run_{run_id}"
        run.checkpoint = {**(run.checkpoint or {}), "surface_id": surface_id}
        await self._db.flush()

        await self._audit.log(
            user_id=run.user_id,
            action_type="run_started",
            plan_id=run.plan_id,
            execution_id=run.run_id,
            summary=f"Run {run_id} started",
            workspace_id=run.workspace_id,
        )
        await self._emit_event(
            "run.started",
            run.user_id,
            {
                "run_id": run_id,
                "plan_id": run.plan_id,
            },
            workspace_id=run.workspace_id,
        )

        # Emit plan_ready so the frontend knows steps are populated and execution begins
        if surface_id:
            all_steps = await self._get_all_steps(run.run_id)
            plan_ready_steps = [_step_to_state(s, status_override="pending") for s in all_steps]
            await self._emit_surface_update(
                surface_id=surface_id,
                user_id=run.user_id,
                phase="plan_ready",
                steps=plan_ready_steps,
                workspace_id=run.workspace_id,
            )

        cancel_event = asyncio.Event()
        self._cancel_events[run.run_id] = cancel_event

        try:
            # Enforce timeout for background runs to prevent indefinite hangs
            timeout = run.timeout_seconds or (600 if run.source == "background" else None)
            if timeout:
                await asyncio.wait_for(
                    self._execute_dag(run, surface_id=surface_id, cancel_event=cancel_event),
                    timeout=timeout,
                )
            else:
                await self._execute_dag(run, surface_id=surface_id, cancel_event=cancel_event)
        except asyncio.TimeoutError:
            transition_run(run, "timed_out")
            run.completed_at = datetime.now(timezone.utc)
            run.error = {
                "type": "TimeoutError",
                "message": f"Run timed out after {timeout}s",
            }
            logger.warning("Run %s timed out after %ds", run_id, timeout)
            await self._emit_event(
                "run.timed_out",
                run.user_id,
                {"run_id": run_id, "timeout": timeout},
                workspace_id=run.workspace_id,
            )
        except Exception as exc:
            transition_run(run, "failed")
            run.completed_at = datetime.now(timezone.utc)
            # run.error is rendered in execution surfaces + run history (client-facing).
            # Store the safe message + code + correlation id; raw str(exc) → logs only.
            safe = _safe_error_fields(exc)
            run.error = {"type": "execution_error", **safe}
            logger.error("Run %s failed: %s", run_id, exc, exc_info=True)
            await self._emit_event(
                "run.failed",
                run.user_id,
                {
                    "run_id": run_id,
                    "error": safe["message"],
                    "error_code": safe["error_code"],
                    "correlation_id": safe["correlation_id"],
                },
                workspace_id=run.workspace_id,
            )
        finally:
            self._cancel_events.pop(run.run_id, None)
            # Finalize and persist the trace
            await self._finalize_trace(run)

        # Record Prometheus metrics
        try:
            from src.services.metrics_service import MetricsService

            MetricsService.record_execution_completed(run.status)
        except Exception:
            pass

        await self._reconcile_plan_status(run)
        await self._db.commit()
        return run

    async def resume_run(self, run_id: str) -> TaskRun:
        """Resume a paused/awaiting run from its last checkpoint.

        If the run has been paused for >30 minutes and a ContextBuilder is
        available, the context is refreshed before resuming to avoid
        stale data from the original run creation.
        """
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        resumable = ("paused", "awaiting_approval", "awaiting_input")
        if run.status not in resumable:
            raise ValueError(f"Run {run_id} is not resumable (status={run.status})")

        # Refresh stale context if paused for >30 minutes
        pause_duration = (
            datetime.now(timezone.utc) - (run.started_at or run.created_at)
        ).total_seconds()
        if pause_duration > 1800 and hasattr(self, "_context_builder") and self._context_builder:
            try:
                fresh_pack = await self._context_builder.build(
                    user_id=run.user_id,
                    query=(run.context_pack_json or {}).get("task_summary", "")[:500],
                    workspace_id=run.workspace_id,
                )
                run.context_pack_json = fresh_pack.model_dump()
                logger.info(
                    "Refreshed stale context for run %s (paused %ds)",
                    run_id,
                    int(pause_duration),
                )
            except Exception:
                logger.debug("Context refresh failed, using cached", exc_info=True)

        # Validate checkpoint consistency
        if run.checkpoint:
            cp_completed = set(run.checkpoint.get("completed_steps", {}).keys())
            actual_steps = await self._get_all_steps(run.run_id)
            actual_completed = {s.step_id for s in actual_steps if s.status == "completed"}
            if cp_completed != actual_completed:
                logger.warning(
                    "Checkpoint/DB mismatch for run %s: checkpoint=%d completed, DB=%d completed",
                    run.run_id,
                    len(cp_completed),
                    len(actual_completed),
                )

        transition_run(run, "running")
        await self._db.flush()

        # Each resume is its own observability cycle with a fresh trace_id.
        # TraceStore._store_to_db does INSERT (not upsert), so reusing the
        # initial run's trace_id here would violate the traces PK. Segments
        # stay correlatable via run_id. run.trace_id keeps pointing at the
        # initial trace so routes_history / evidence_bundle consumers that
        # expect a single canonical pointer still work.
        trace = JarvisTrace(
            trace_id=f"trace_{ULID()}",
            trigger="execution:resume",
        )
        self._active_traces[run.run_id] = trace
        if not run.trace_id:
            run.trace_id = trace.trace_id
            await self._db.flush()

        cancel_event = asyncio.Event()
        self._cancel_events[run.run_id] = cancel_event

        surface_id = (run.checkpoint or {}).get("surface_id")
        try:
            await self._execute_dag(run, surface_id=surface_id, cancel_event=cancel_event)
        except Exception as exc:
            transition_run(run, "failed")
            run.completed_at = datetime.now(timezone.utc)
            # Client-facing (served by the history API) — safe message + code only;
            # raw str(exc) goes to logs.
            safe = _safe_error_fields(exc)
            run.error = {"type": "resume_error", **safe}
            logger.error("Resume run %s failed: %s", run.run_id, exc, exc_info=True)
        finally:
            self._cancel_events.pop(run.run_id, None)
            await self._finalize_trace(run)

        await self._reconcile_plan_status(run)
        await self._db.commit()
        return run

    async def pause_run(self, run_id: str, reason: str = "manual_pause") -> TaskRun:
        """Pause a running execution."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        transition_run(run, "paused")
        await self._checkpoint(run, step_id=None, reason=reason)
        await self._db.commit()
        return run

    async def cancel_run(self, run_id: str) -> TaskRun:
        """Cancel a run and all pending/running steps.

        Signals the cancellation event so that in-progress agent loops
        exit gracefully between tool rounds.
        """
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        # Signal cancellation to running agent loops
        cancel_event = self._cancel_events.get(run_id)
        if cancel_event:
            cancel_event.set()

        transition_run(run, "cancelled")
        run.completed_at = datetime.now(timezone.utc)

        # Mark all non-completed steps as skipped or cancelled
        steps_result = await self._db.execute(
            select(TaskStep).where(
                TaskStep.run_id == run_id,
                TaskStep.status.in_(["pending", "ready", "running"]),
            )
        )
        for step in steps_result.scalars().all():
            if step.status == "running":
                transition_step(step, "cancelled")
            else:
                transition_step(step, "skipped")
            await self._emit_event(
                "step.skipped",
                run.user_id,
                {"run_id": run_id, "step_id": step.step_id},
                workspace_id=run.workspace_id,
            )

        await self._reconcile_plan_status(run)
        await self._db.commit()
        await self._emit_event(
            "run.cancelled",
            run.user_id,
            {"run_id": run_id},
            workspace_id=run.workspace_id,
        )
        return run

    async def _execute_dag(
        self,
        run: TaskRun,
        surface_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """Main DAG execution loop."""
        _dag_start = time.monotonic()
        while True:
            ready_steps = await self._get_ready_steps(run.run_id)
            if not ready_steps:
                # Check if all steps are done
                all_steps = await self._get_all_steps(run.run_id)
                pending = [s for s in all_steps if s.status in ("pending", "ready", "running")]
                if not pending:
                    # Use partially_completed before verification (if verifier exists)
                    if self._verifier:
                        transition_run(run, "partially_completed")
                    else:
                        transition_run(run, "completed")
                    run.completed_at = datetime.now(timezone.utc)
                    await self._emit_event(
                        "run_completed",
                        run.user_id,
                        {"run_id": run.run_id, "plan_id": run.plan_id},
                        workspace_id=run.workspace_id,
                    )
                    # Run verifier if available
                    if self._verifier:
                        await self._run_verification(run)
                    # Writeback memories from execution results
                    await self._writeback_memories(run)
                    if surface_id:
                        _comp_steps = await self._get_all_steps(run.run_id)
                        _final_states = [_step_to_state(s) for s in _comp_steps]
                        _findings = [
                            str(s.output_data.get("result", ""))
                            for s in _comp_steps
                            if s.output_data and s.output_data.get("result")
                        ]
                        await self._emit_surface_update(
                            surface_id=surface_id,
                            user_id=run.user_id,
                            phase="completed",
                            steps=_final_states,
                            progress=f"{len(_comp_steps)}/{len(_comp_steps)} steps",
                            results=ResultSummary(key_findings=_findings[:5]),
                            workspace_id=run.workspace_id,
                        )
                        # Emit a lightweight summary card for the workspace
                        # feed and archive the run surface.
                        await self._emit_summary_surface(run, surface_id)
                    break
                # If there are pending steps but none ready, we're blocked
                failed = [s for s in all_steps if s.status == "failed"]
                if failed:
                    transition_run(run, "failed")
                    run.completed_at = datetime.now(timezone.utc)
                    run.error = {
                        "message": f"{len(failed)} step(s) failed",
                        "failed_steps": [s.step_id for s in failed],
                    }
                    if surface_id:
                        _fail_steps = await self._get_all_steps(run.run_id)
                        _fail_states = [_step_to_state(s) for s in _fail_steps]
                        await self._emit_surface_update(
                            surface_id=surface_id,
                            user_id=run.user_id,
                            phase="failed",
                            steps=_fail_states,
                            progress=f"{len(failed)} step(s) failed",
                            workspace_id=run.workspace_id,
                        )
                        await self._emit_summary_surface(run, surface_id)
                    break
                # Must be waiting for approval or external event
                break

            # Execute ready steps sequentially (shared AsyncSession is not
            # safe for concurrent coroutines — parallel gather caused silent
            # step failures and permanently stuck runs).
            run.current_step_ids = [s.step_id for s in ready_steps]
            await self._db.flush()

            # Surface update: executing phase
            if surface_id:
                _all_for_surface = await self._get_all_steps(run.run_id)
                _step_states = [
                    _step_to_state(
                        s,
                        status_override="executing"
                        if s.step_id in (run.current_step_ids or [])
                        else None,
                    )
                    for s in _all_for_surface
                ]
                _done_count = sum(1 for s in _all_for_surface if s.status == "completed")
                await self._emit_surface_update(
                    surface_id=surface_id,
                    user_id=run.user_id,
                    phase="executing",
                    steps=_step_states,
                    current_step=ready_steps[0].step_id if ready_steps else None,
                    progress=f"{_done_count}/{len(_all_for_surface)} steps",
                    workspace_id=run.workspace_id,
                )

            for step in ready_steps:
                try:
                    await self._execute_step(
                        run, step, surface_id=surface_id, cancel_event=cancel_event
                    )
                except CancellationRequested:
                    # Run was cancelled — cancel_run() already set the run status
                    return
                except Exception:
                    logger.error("Step %s raised unexpectedly", step.step_id, exc_info=True)

            # Check if run was paused by an approval gate
            await self._db.refresh(run)
            if run.status in ("paused", "awaiting_approval"):
                break

        _dag_elapsed = time.monotonic() - _dag_start
        if _dag_elapsed > 120:
            logger.warning(
                "Long DAG execution: run %s took %.1fs — "
                "consider db_factory pattern for connection pool safety",
                run.run_id,
                _dag_elapsed,
            )

    async def _execute_step(
        self,
        run: TaskRun,
        step: TaskStep,
        surface_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """Execute a single step, with single TrustEngine approval gate."""
        already_approved = step.status == "running"

        if not already_approved:
            capability = (step.input_data or {}).get(
                "capability", (step.input_data or {}).get("task_type", "")
            )

            # ── Fail-closed contract guard ───────────────────────────────
            # The autonomous path MUST be gated by the TrustEngine. Two
            # conditions leave a step unevaluatable and therefore unsafe to
            # auto-execute:
            #   • no TrustEngine — create_graph_executor and runtime always
            #     supply one (TrustEngine construction cannot fail), so an
            #     absent engine here is a wiring/misconfiguration, never a
            #     normal mode. We refuse to fall back to an ungated legacy
            #     approval path (SVC-P3-1).
            #   • empty capability — the Planner ALWAYS emits a capability per
            #     PlanStep (see CLAUDE.md), so a missing one is contract drift.
            # In either case we fail the step loudly rather than auto-execute a
            # potential external write with NO risk assessment and NO approval.
            if not self._trust_engine or not capability:
                reason = "missing TrustEngine" if not self._trust_engine else "empty capability"
                logger.error(
                    "Step %s reached approval gate with %s "
                    "(input_data keys=%s) — failing closed as contract violation",
                    step.step_id,
                    reason,
                    sorted((step.input_data or {}).keys()),
                )
                transition_step(step, "running")
                transition_step(step, "failed")
                step.completed_at = datetime.now(timezone.utc)
                step.output_data = {"error": f"contract_violation: {reason}"}
                step.error = {
                    "message": (
                        f"Step cannot be gated ({reason}); refusing to execute "
                        "ungated (TrustEngine contract violation)"
                    ),
                    "final": True,
                }
                await self._db.flush()
                await self._emit_event(
                    "step.failed",
                    run.user_id,
                    {
                        "run_id": run.run_id,
                        "step_id": step.step_id,
                        "error": f"contract_violation: {reason}",
                    },
                    workspace_id=run.workspace_id,
                )
                return

            # ── Single TrustEngine gate ──────────────────────────────────
            # The TrustEngine and capability are both guaranteed present by the
            # fail-closed guard above, so the gate runs unconditionally — there
            # is no ungated fall-through path out of this block.
            risk = await self._assess_step_risk(capability, step, run)
            decision = await self._trust_engine.evaluate(
                capability, risk, workspace_id=run.workspace_id or ""
            )

            if decision.decision == "approval_required":
                await self._create_approval_and_pause(
                    run, step, capability, risk, decision, surface_id=surface_id
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

            step_timeout = step.timeout_seconds or 120
            t0 = time.monotonic()
            try:
                output = await asyncio.wait_for(
                    self._run_step_action(step, run, cancel_event=cancel_event),
                    timeout=step_timeout,
                )
                elapsed_ms = int((time.monotonic() - t0) * 1000)
            except asyncio.TimeoutError:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                transition_step(step, "timed_out")
                step.error = {"message": f"Step timed out after {step_timeout}s"}
                step.completed_at = datetime.now(timezone.utc)
                await self._db.flush()
                logger.warning("Step %s timed out after %ds", step.step_id, step_timeout)
                return
            except CancellationRequested:
                transition_step(step, "cancelled")
                step.completed_at = datetime.now(timezone.utc)
                await self._db.flush()
                raise
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                await self._handle_step_failure(run, step, exc, elapsed_ms, surface_id=surface_id)
                return

            if decision.decision == "auto_execute_notify":
                await self._notify_auto_executed(run, step, risk, output)

            await self._finalize_step(run, step, output, elapsed_ms)
            return

        # ── Common execution path (step resumed after approval) ──────
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

        step_timeout = step.timeout_seconds or 120
        t0 = time.monotonic()
        try:
            output = await asyncio.wait_for(
                self._run_step_action(step, run, cancel_event=cancel_event),
                timeout=step_timeout,
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            transition_step(step, "timed_out")
            step.error = {"message": f"Step timed out after {step_timeout}s"}
            step.completed_at = datetime.now(timezone.utc)
            await self._db.flush()
            logger.warning("Step %s timed out after %ds", step.step_id, step_timeout)
            return
        except CancellationRequested:
            transition_step(step, "cancelled")
            step.completed_at = datetime.now(timezone.utc)
            await self._db.flush()
            raise
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            await self._handle_step_failure(run, step, exc, elapsed_ms, surface_id=surface_id)
            return

        await self._finalize_step(run, step, output, elapsed_ms)

    # ── TrustEngine helper methods ───────────────────────────────────

    async def _assess_step_risk(
        self, capability: str, step: TaskStep, run: TaskRun
    ) -> RiskAssessment:
        """Call get_or_assess_risk with appropriate context."""
        try:
            return await get_or_assess_risk(
                capability=capability,
                step_input=step.input_data or {},
                user_context={"user_id": run.user_id},
                workspace_id=run.workspace_id or "",
                client=self._client,
                redis=self._redis,
            )
        except Exception:
            logger.warning(
                "Risk assessment failed for %s, failing closed to high (forces approval)",
                capability,
                exc_info=True,
            )
            # Fail closed: unknown risk → high → approval_required at every trust level.
            return RiskAssessment(
                risk_level="high",
                reasoning="Fallback — risk assessment unavailable, failing closed to high",
                reversible=False,
            )

    async def _create_approval_and_pause(
        self,
        run: TaskRun,
        step: TaskStep,
        capability: str,
        risk: RiskAssessment,
        decision: PolicyDecision,
        surface_id: str | None = None,
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
                logger.warning("Failed to notify for step approval", exc_info=True)

        # Surface update: approval needed
        if surface_id:
            from src.contracts import ApprovalContext

            await self._emit_surface_update(
                surface_id=surface_id,
                user_id=run.user_id,
                phase="approval_needed",
                approval=ApprovalContext(
                    approval_id=approval.approval_id,
                    step_description=step.name or capability,
                    risk_level=risk.risk_level,
                    trust_level=decision.trust_level,
                    expires_at=(approval.expires_at.isoformat() if approval.expires_at else None),
                    triggering_step_id=step.step_id,
                    graduation_hint=decision.justification or "",
                    risk_reasoning=risk.reasoning,
                    trust_context=decision.justification or "",
                    reversible=risk.reversible,
                    blast_radius=risk.blast_radius,
                    effective_trust_level=decision.effective_trust_level,
                    approved_count=decision.approved_count,
                    rejected_count=decision.rejected_count,
                ),
                workspace_id=run.workspace_id,
            )

    async def _notify_auto_executed(
        self,
        run: TaskRun,
        step: TaskStep,
        risk: RiskAssessment,
        output: dict | None,
    ) -> None:
        """Send post-execution notification for auto_execute_notify."""
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
            logger.warning("Failed to send auto_execute notification", exc_info=True)

    async def _handle_step_failure(
        self,
        run: TaskRun,
        step: TaskStep,
        exc: Exception,
        elapsed_ms: int,
        surface_id: str | None = None,
    ) -> None:
        """Handle step execution failure with retry logic."""
        step.retry_count += 1
        if step.retry_count < step.max_retries:
            delay = _compute_retry_delay(step.retry_count)
            logger.warning(
                "Step %s failed (attempt %d/%d), retrying in %ds: %s",
                step.step_id,
                step.retry_count,
                step.max_retries,
                delay,
                exc,
            )
            transition_step(step, "failed")
            transition_step(step, "pending")  # Retry: failed → pending
            # step.error is surfaced in execution surfaces — keep it sanitized.
            safe = _safe_error_fields(exc)
            step.error = {
                "attempt": step.retry_count,
                "message": safe["message"],
                "error_code": safe["error_code"],
                "correlation_id": safe["correlation_id"],
                "retry_after_seconds": delay,
            }
            await self._db.flush()
            await asyncio.sleep(delay)
        else:
            logger.error(
                "Step %s permanently failed after %dms: %s",
                step.step_id,
                elapsed_ms,
                exc,
            )
            transition_step(step, "failed")
            # step.output_data is rendered in execution surfaces + run history,
            # and step.error feeds the surface error line — both client-facing.
            # Store the safe message + code + correlation id; raw str(exc) → logs/trace only.
            safe = _safe_error_fields(exc)
            step.output_data = {
                "error": safe["message"],
                "error_code": safe["error_code"],
                "correlation_id": safe["correlation_id"],
            }
            step.completed_at = datetime.now(timezone.utc)
            step.error = {
                "message": safe["message"],
                "error_code": safe["error_code"],
                "correlation_id": safe["correlation_id"],
                "final": True,
            }
            await self._emit_event(
                "step.failed",
                run.user_id,
                {
                    "run_id": run.run_id,
                    "step_id": step.step_id,
                    "error": safe["message"],
                    "error_code": safe["error_code"],
                    "correlation_id": safe["correlation_id"],
                    "duration_ms": elapsed_ms,
                },
                workspace_id=run.workspace_id,
            )
            if surface_id:
                all_steps = await self._get_all_steps(run.run_id)
                step_states = [
                    _step_to_state(
                        s,
                        status_override="failed" if s.step_id == step.step_id else None,
                    )
                    for s in all_steps
                ]
                await self._emit_surface_update(
                    surface_id=surface_id,
                    user_id=run.user_id,
                    phase="failed",
                    steps=step_states,
                    progress=f"Step {step.step_id} permanently failed",
                    workspace_id=run.workspace_id,
                )
        await self._db.flush()

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
                "task_id": step.task_id,
                "duration_ms": result.duration_ms,
            },
            workspace_id=run.workspace_id,
        )

        # Emit surface.updated for A2UI live streaming
        if output and any(k in output for k in ("draft", "report", "summary", "result", "view")):
            await self._emit_event(
                "surface_created",
                run.user_id,
                {
                    "run_id": run.run_id,
                    "step_id": step.step_id,
                    "surface_type": "step_output",
                    "preview": str(output.get("result", output.get("summary", "")))[:200],
                },
                workspace_id=run.workspace_id,
            )

    async def _run_step_action(
        self,
        step: TaskStep,
        run: TaskRun,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """Execute the actual action for a step.

        Routes to agent loop if dependencies are available, otherwise uses
        a minimal single-turn Claude fallback.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))

        await self._emit_event(
            "tool_call_started",
            run.user_id,
            {"run_id": run.run_id, "step_id": step.step_id, "tool_name": task_type},
            workspace_id=run.workspace_id,
        )

        # Check if agent loop dependencies are available
        if self._db_factory and self._execute_tool_fn and self._budget:
            return await self._run_step_via_agent_loop(step, run, cancel_event=cancel_event)

        # Fallback: minimal single-turn Claude call
        return await self._minimal_claude_action(step, run)

    async def _minimal_claude_action(self, step: TaskStep, run: TaskRun) -> dict:
        """Minimal single-turn Claude action without tool discovery.

        Used as fallback when agent loop dependencies are not available.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
        context_prompt = await self._build_step_context(run, step)

        goal = input_data.get("goal", input_data.get("context", ""))
        parts = [f"Task type: {task_type}"]
        if goal:
            parts.append(f"Goal: {goal}")
        for key, value in input_data.items():
            if key not in ("task_type", "goal", "context"):
                parts.append(f"{key}: {value}")
        if context_prompt:
            parts.append(f"\n--- Background ---\n{context_prompt}")

        system = (
            f"You are Jarvis's task execution engine handling a '{task_type}' step. "
            "Complete the task described below. "
            'Respond with JSON: {"status": "completed", "result": "...", "details": {...}}'
        )

        response = await self._client.messages.create(
            model=self._settings.resolved_model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": "\n".join(parts)}],
        )

        try:
            return parse_llm_json(response.content[0].text)
        except json.JSONDecodeError:
            return {"status": "completed", "result": response.content[0].text}

    async def _build_operator_tools(self) -> list[dict]:
        """Build Claude API tool definitions filtered by Operator's capability scope."""
        if not self._tool_registry:
            return []

        from src.orchestrator.agents import AGENTS
        from src.tools.schemas import TOOL_INPUT_MODELS

        operator = AGENTS.get("operator")
        if not operator:
            return []

        scope = operator.capability_scope
        tools = []
        seen = set()

        # Internal tools from TOOL_INPUT_MODELS
        for tool_name, model_cls in TOOL_INPUT_MODELS.items():
            tool_def = await self._tool_registry.get_tool(tool_name)
            if tool_def and tool_def.capability and tool_def.capability in scope:
                schema = model_cls.model_json_schema()
                tools.append(
                    {
                        "name": tool_name,
                        "description": (
                            model_cls.__doc__.strip() if model_cls.__doc__ else tool_name
                        ),
                        "input_schema": schema,
                    }
                )
                seen.add(tool_name)

        # External tools from registry
        try:
            all_tools = await self._tool_registry.list_tools(enabled_only=True)
            for tool_def in all_tools:
                if (
                    tool_def.name not in seen
                    and tool_def.capability
                    and tool_def.capability in scope
                ):
                    tools.append(
                        {
                            "name": tool_def.name,
                            "description": tool_def.description or tool_def.name,
                            "input_schema": tool_def.input_schema or {"type": "object"},
                        }
                    )
                    seen.add(tool_def.name)
        except Exception:
            logger.debug("Failed to list external tools", exc_info=True)

        return tools

    async def _run_step_via_agent_loop(
        self,
        step: TaskStep,
        run: TaskRun,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """Execute a step via the Operator agent loop with full tool discovery."""
        from src.orchestrator.agent_loop import (
            LoopDone,
            LoopError,
            LoopToolCall,
            agent_loop,
        )
        from src.orchestrator.agents import AGENTS

        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
        goal = input_data.get("goal", input_data.get("context", ""))

        # Build message from step input
        message_parts = [f"Task type: {task_type}"]
        if goal:
            message_parts.append(f"Goal: {goal}")
        for key, value in input_data.items():
            if key not in ("task_type", "goal", "context"):
                message_parts.append(f"{key}: {value}")

        message = "\n".join(message_parts)

        # Inject completed predecessor step outputs so the operator sees
        # what earlier agents (e.g. Perceiver) read or produced.
        all_steps = await self._get_all_steps(run.run_id)
        prior_parts: list[str] = []
        for s in all_steps:
            if s.step_id == step.step_id:
                continue
            if s.status != "completed" or not s.output_data:
                continue
            result_text = s.output_data.get("result", "")
            if not result_text:
                continue
            cap = (s.input_data or {}).get("capability", "unknown")
            desc = (s.input_data or {}).get("goal", cap)
            prior_parts.append(f"[{desc}]:\n{str(result_text)}")
        if prior_parts:
            message += (
                "\n\n--- Prior step results ---\n"
                + "\n\n".join(prior_parts)
                + "\n--- End of prior step results ---\n"
            )

        # Get context
        context_prompt = await self._build_step_context(run, step)

        # Resolve operator agent
        operator = AGENTS.get("operator")
        if not operator:
            return {
                "status": "completed",
                "result": "Operator agent not found",
                "errors": ["Operator agent not configured"],
            }

        # Build system blocks
        system_blocks = [{"type": "text", "text": operator.prompt}]
        if context_prompt:
            system_blocks.append({"type": "text", "text": f"\n--- Context ---\n{context_prompt}"})

        # Build tools list
        tools = await self._build_operator_tools()

        # Collect events from agent loop
        text = ""
        tools_called = []
        errors = []

        async for event in agent_loop(
            client=self._client,
            agent=operator,
            model=self._settings.resolved_model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=run.user_id,
            workspace_id=run.workspace_id or "",
            db_factory=self._db_factory,
            services=None,
            budget=self._budget,
            trace=self._active_traces.get(run.run_id),
            execute_tool_fn=self._execute_tool_fn,
            max_tool_rounds=10,
            stream=False,
            circuit_breaker=self._circuit_breaker,
            run_id=run.run_id,
            cancel_event=cancel_event,
        ):
            if isinstance(event, LoopDone):
                text = event.text
                tools_called = event.tools_called
            elif isinstance(event, LoopError):
                errors.append(event.message)
            elif isinstance(event, LoopToolCall):
                pass  # Already tracked in LoopDone.tools_called

        return {
            "status": "completed",
            "result": text,
            "tools_called": tools_called,
            "errors": errors,
        }

    async def _finalize_trace(self, run: TaskRun) -> None:
        """Finalize and persist the JarvisTrace for a completed/failed run.

        Also writes the aggregate token/cost rollup onto the TaskRun row so
        history views can render observability metrics without joining the
        Trace table (and so that the detail endpoint has a deterministic
        non-zero result even if the trace persistence fails).
        """
        trace = self._active_traces.pop(run.run_id, None)
        if not trace:
            return
        trace.finish()
        input_t, output_t = trace.total_tokens()
        total_cost = 0.0
        try:
            for span in trace.spans:
                total_cost += float(getattr(span, "cost_usd", 0.0) or 0.0)
        except Exception:
            total_cost = 0.0

        # Roll up onto the run row. Safe to set even when trace persistence
        # fails — the numbers reflect what agent_loop actually recorded.
        try:
            run.input_tokens = int(input_t or 0)
            run.output_tokens = int(output_t or 0)
            run.cost_usd = round(float(total_cost), 6)
        except Exception:
            logger.debug("Failed to roll up token usage onto run %s", run.run_id, exc_info=True)

        logger.info(
            "run_trace_finalized",
            extra={
                "run_id": run.run_id,
                "trace_id": trace.trace_id,
                "spans": len(trace.spans),
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cost_usd": total_cost,
            },
        )
        if self._trace_store:
            try:
                await self._trace_store.store_trace(
                    trace.to_dict(),
                    user_id=run.user_id,
                    workspace_id=run.workspace_id or "",
                    run_id=run.run_id,
                )
            except Exception:
                logger.warning(
                    "Failed to persist trace %s for run %s",
                    trace.trace_id,
                    run.run_id,
                    exc_info=True,
                )

    async def _build_step_context(self, run: TaskRun, step: TaskStep) -> str:
        """Build context prompt for a step using ContextBuilder."""
        if not self._context_builder:
            return ""
        try:
            input_data = step.input_data or {}
            query = input_data.get("goal", input_data.get("context", ""))
            task_type = input_data.get("task_type")
            pack = await self._context_builder.build(
                user_id=run.user_id,
                query=query or "",
                task_type=task_type,
            )
            from src.services.context_builder import ContextBuilder

            return ContextBuilder.to_prompt(pack)
        except Exception:
            logger.debug("ContextBuilder failed for step %s", step.step_id, exc_info=True)
            return ""

    async def _get_ready_steps(self, run_id: str) -> list[TaskStep]:
        """Get steps whose dependencies are all completed.

        Also picks up steps already in 'ready' state (e.g. from a previous
        iteration where execution failed before the step could start) and
        steps in 'running' state from approval resumption (the approval
        handler transitions waiting_approval → running before the scheduler
        resumes the DAG).
        """
        all_steps = await self._get_all_steps(run_id)
        completed_ids = {s.step_id for s in all_steps if s.status == "completed"}

        ready = []
        needs_flush = False
        for step in all_steps:
            if step.status == "ready":
                ready.append(step)
            elif step.status == "running":
                # Resumed-from-approval: step was transitioned to 'running'
                # by the approval handler but not yet executed.
                ready.append(step)
            elif step.status == "pending":
                deps = step.depends_on or []
                if all(dep_id in completed_ids for dep_id in deps):
                    transition_step(step, "ready")
                    ready.append(step)
                    needs_flush = True

        if needs_flush:
            await self._db.flush()
        return ready

    async def _get_all_steps(self, run_id: str) -> list[TaskStep]:
        """Get all steps for a run."""
        result = await self._db.execute(
            select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.created_at)
        )
        return list(result.scalars().all())

    async def _resolve_step_references(self, step: TaskStep, run_id: str) -> dict:
        """Resolve {task_id}.output.field references in step input_data.

        Enables declarative wiring between DAG steps: a downstream step
        can reference an upstream step's output by task_id.
        """
        input_data = dict(step.input_data or {})
        all_steps = await self._get_all_steps(run_id)
        outputs_by_task = {s.task_id: s.output_data for s in all_steps if s.output_data}

        def resolve(value):
            if isinstance(value, str) and value.startswith("{") and "}.output." in value:
                ref, _, field = value[1:].partition("}.output.")
                source = outputs_by_task.get(ref)
                if source is None or not isinstance(source, dict):
                    logger.warning(
                        "Step reference unresolved: task '%s' not found in "
                        "completed steps (run_id=%s, step=%s)",
                        ref,
                        run_id,
                        step.step_id,
                    )
                    return value
                if field not in source:
                    logger.warning(
                        "Step reference field missing: '%s' not in task '%s' "
                        "output (run_id=%s, step=%s, available_keys=%s)",
                        field,
                        ref,
                        run_id,
                        step.step_id,
                        list(source.keys()),
                    )
                    return value
                return source[field]
            return value

        resolved = {k: resolve(v) for k, v in input_data.items()}
        unresolved = [k for k, v in resolved.items() if isinstance(v, str) and "}.output." in v]
        if unresolved:
            logger.warning(
                "Step %s has %d unresolved reference(s): %s",
                step.step_id,
                len(unresolved),
                unresolved,
            )
        return resolved

    async def _checkpoint(self, run: TaskRun, step_id: str | None, reason: str) -> None:
        """Save a rich checkpoint with completed step outputs."""
        # Collect completed step outputs for checkpoint context
        completed_outputs = {}
        try:
            all_steps = await self._get_all_steps(run.run_id)
            completed_outputs = {
                s.step_id: {
                    "task_id": s.task_id,
                    "status": s.status,
                    "output_summary": str(s.output_data) if s.output_data else None,
                }
                for s in all_steps
                if s.status == "completed"
            }
        except Exception:
            pass  # Non-critical — checkpoint still saved without outputs

        snapshot = {
            "status": run.status,
            "current_step_ids": run.current_step_ids,
            "completed_steps": completed_outputs,
            "checkpoint_at": datetime.now(timezone.utc).isoformat(),
        }
        checkpoint = TaskCheckpoint(
            checkpoint_id=f"ckpt_{ULID()}",
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            step_id=step_id,
            reason=reason,
            state_snapshot=snapshot,
        )
        self._db.add(checkpoint)
        run.checkpoint = snapshot
        await self._db.flush()

    async def _writeback_memories(self, run: TaskRun) -> None:
        """Extract and store memories from completed execution results."""
        if not self._memory_service:
            return
        try:
            all_steps = await self._get_all_steps(run.run_id)
            completed = [s for s in all_steps if s.status == "completed" and s.output_data]
            if not completed:
                return
            parts = [f"Completed plan: {run.plan_id}"]
            for step in completed[:5]:
                parts.append(f"- {step.task_id}: {json.dumps(step.output_data)}")
            await self._memory_service.extract_and_store(
                user_id=run.user_id,
                source_text="\n".join(parts),
                source_event_ids=[run.run_id],
                workspace_id=run.workspace_id,
            )
        except Exception:
            logger.warning(
                "Memory writeback failed for run %s — execution memories not stored",
                run.run_id,
                exc_info=True,
            )

    async def _run_verification(self, run: TaskRun) -> None:
        """Run verification on a completed run."""
        try:
            # Load success conditions from the plan
            plan_result = await self._db.execute(select(Plan).where(Plan.plan_id == run.plan_id))
            plan = plan_result.scalar_one_or_none()
            conditions = plan.success_conditions if plan else None

            result = await self._verifier.verify_run(run.run_id, conditions)
            # Store verdict in checkpoint
            await self._checkpoint(run, None, "verification")
            run.checkpoint = {
                **(run.checkpoint or {}),
                "verification": {
                    "verdict": result.verdict.value,
                    "score": result.score,
                    "details": result.details,
                },
            }
            if result.verdict.value == "failed":
                transition_run(run, "failed")
                run.error = {"verification_failed": result.details}
                logger.warning("Run %s failed verification: %s", run.run_id, result.details)
            else:
                # Verification passed — promote from partially_completed to completed
                if run.status == "partially_completed":
                    transition_run(run, "completed")
        except Exception:
            logger.warning("Verification failed for run %s", run.run_id, exc_info=True)

    # Map a terminal run status to the status its parent Plan should take.
    _RUN_STATUS_TO_PLAN_STATUS = {
        "completed": "completed",
        "partially_completed": "completed",
        "failed": "failed",
        "timed_out": "failed",
        "cancelled": "cancelled",
    }

    async def _reconcile_plan_status(self, run: TaskRun) -> None:
        """Mirror a terminal run status onto its parent Plan.

        Without this, a Plan stays in 'created'/'executing' forever after its
        run finishes. Stale 'created' plans then get injected into every daily
        briefing (the "phantom critical security alert" regression). Non-terminal
        run statuses (paused, awaiting_approval) are intentionally skipped, and
        plans already in a terminal state are left untouched so a late run can't
        resurrect them.
        """
        target = self._RUN_STATUS_TO_PLAN_STATUS.get(run.status)
        if not target or not run.plan_id:
            return
        result = await self._db.execute(select(Plan).where(Plan.plan_id == run.plan_id))
        plan = result.scalar_one_or_none()
        if plan and plan.status not in ("completed", "failed", "cancelled"):
            plan.status = target

    async def _emit_event(
        self,
        event_type: str,
        user_id: str,
        payload: dict,
        workspace_id: str | None = None,
    ) -> None:
        """Forward to the SurfaceEmitter collaborator (SVC-P1-3)."""
        await self._surface_emitter.emit_event(event_type, user_id, payload, workspace_id)

    async def _publish_progress(self, run_id: str, data: dict) -> None:
        """Forward to the SurfaceEmitter collaborator (SVC-P1-3)."""
        await self._surface_emitter.publish_progress(run_id, data)

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
        workspace_id: str | None = None,
    ) -> None:
        """Forward to the SurfaceEmitter collaborator (SVC-P1-3)."""
        await self._surface_emitter.emit_surface_update(
            surface_id,
            user_id,
            phase,
            steps,
            current_step,
            progress,
            approval,
            results,
            workspace_id,
        )

    async def _emit_summary_surface(
        self,
        run: TaskRun,
        run_surface_id: str,
    ) -> None:
        """Forward to the SurfaceEmitter collaborator (SVC-P1-3)."""
        await self._surface_emitter.emit_summary_surface(run, run_surface_id)

    @staticmethod
    def _build_graph_definition(tasks: list[PlanTask]) -> dict:
        """Build a graph definition from plan tasks."""
        nodes = []
        edges = []
        for task in tasks:
            nodes.append(
                {
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                }
            )
            deps = task.depends_on if isinstance(task.depends_on, list) else []
            for dep_id in deps:
                edges.append({"from": dep_id, "to": task.task_id})
        return {"nodes": nodes, "edges": edges}
