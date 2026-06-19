"""DAG-based execution engine with checkpoints and approval gates.

Replaces the sequential loop in Operator with a proper graph executor
that resolves dependencies, runs independent steps in parallel,
checkpoints after each step, and pauses at approval gates.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.connectors.mcp_bridge import close_turn_sessions
from src.contracts import PolicyDecision, ResultSummary, StepResult
from src.integrations.turn_scope import turn_scope
from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskRun, TaskStep
from src.orchestrator.agent_loop import CancellationRequested
from src.orchestrator.tracing import JarvisTrace
from src.services.audit import AuditService
from src.services.execution_state import transition_run, transition_step
from src.services.execution_support import (
    _compute_retry_delay,
    _safe_error_fields,
    _step_to_state,
)
from src.services.execution_surface_emitter import SurfaceEmitter
from src.services.outcome_learner import OutcomeLearner
from src.services.risk_assessor import RiskAssessment
from src.services.step_graph_store import StepGraphStore
from src.services.step_runner import StepRunner
from src.services.trust_gate import TrustGate

if TYPE_CHECKING:
    from src.services.context_builder import ContextBuilder
    from src.services.memory_service import MemoryService
    from src.services.tool_registry import ToolRegistry
    from src.services.verifier import Verifier

logger = logging.getLogger(__name__)


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
        world_model=world_model,
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
        world_model=None,
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
        self._world_model = world_model
        self._db_factory = db_factory
        self._execute_tool_fn = execute_tool_fn
        self._budget = budget
        self._circuit_breaker = circuit_breaker
        self._trust_engine = trust_engine
        self._redis = redis
        self._trace_store = trace_store
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._active_traces: dict[str, JarvisTrace] = {}
        # Fire-and-forget best-effort work (e.g. entity learning) that must not
        # hold the run's DB connection. Tracked so tasks aren't GC'd mid-flight.
        self._background_tasks: set[asyncio.Task] = set()
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
        # Step-DAG persistence (build/query/readiness/refs/checkpoint) lives in an
        # injected leaf collaborator; the hub forwards to it via facades so the
        # white-box suite (which calls _get_all_steps/_checkpoint/etc. directly)
        # keeps passing unchanged.
        self._store = StepGraphStore(db=db, context_builder=context_builder)
        # Agentic step execution (Operator agent loop + minimal-Claude fallback)
        # lives in an injected collaborator. db_factory + the per-run trace map
        # are resolved live via providers so the coordinator stays the single
        # source of truth (tests reassign _db_factory; _active_traces is owned here).
        self._runner = StepRunner(
            settings=settings,
            client=self._client,
            store=self._store,
            emitter=self._surface_emitter,
            db_factory_provider=lambda: self._db_factory,
            active_traces_provider=lambda: self._active_traces,
            tool_registry=tool_registry,
            context_builder=context_builder,
            execute_tool_fn=execute_tool_fn,
            budget=budget,
            circuit_breaker=circuit_breaker,
        )
        # The side-effecting helpers of the single TrustEngine approval gate
        # (risk assessment, approval persistence + pause, auto-execute trust
        # feedback) live in an injected collaborator. The gate DECISION itself
        # (TrustEngine.evaluate + the fail-closed contract guard) stays in the
        # step pipeline below; this holds the helpers it calls.
        self._trust_gate = TrustGate(
            db=db,
            client=self._client,
            redis=redis,
            notifier_provider=lambda: self._notifier,
            store=self._store,
            emitter=self._surface_emitter,
        )
        # Post-run learning (memory writeback, entity/graph enrichment,
        # verification + trust penalty) lives in an injected collaborator.
        # Background spawning stays coordinator-owned (injected as a callable);
        # verifier + db_factory resolve via providers so reassigning them after
        # construction propagates (tests do this).
        self._learner = OutcomeLearner(
            settings=settings,
            db=db,
            store=self._store,
            spawn_background=self._spawn_background,
            db_factory_provider=lambda: self._db_factory,
            verifier_provider=lambda: self._verifier,
            memory_service=memory_service,
            world_model=world_model,
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
        """Facade → StepGraphStore.populate_steps."""
        await self._store.populate_steps(run, plan)

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
        async with turn_scope(on_close=close_turn_sessions):
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
            # Reinforce trust: a successful auto-execution graduates trust the
            # same way an explicit user approval does, so the autonomous path
            # learns from its own outcomes (not only from approval prompts).
            risk_level = getattr(risk, "risk_level", risk)
            await self._record_auto_execution_outcome(
                capability, risk_level, run.workspace_id or ""
            )
            # Remember the auto-executed (capability, risk_level) so a later
            # verification failure can reverse this reinforcement (SVC).
            self._remember_auto_executed(run, capability, risk_level)
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
        """Facade → TrustGate.assess_step_risk."""
        return await self._trust_gate.assess_step_risk(capability, step, run)

    async def _create_approval_and_pause(
        self,
        run: TaskRun,
        step: TaskStep,
        capability: str,
        risk: RiskAssessment,
        decision: PolicyDecision,
        surface_id: str | None = None,
    ) -> None:
        """Facade → TrustGate.create_approval_and_pause."""
        await self._trust_gate.create_approval_and_pause(
            run, step, capability, risk, decision, surface_id=surface_id
        )

    async def _notify_auto_executed(
        self,
        run: TaskRun,
        step: TaskStep,
        risk: RiskAssessment,
        output: dict | None,
    ) -> None:
        """Facade → TrustGate.notify_auto_executed."""
        await self._trust_gate.notify_auto_executed(run, step, risk, output)

    async def _record_auto_execution_outcome(
        self, capability: str, risk_level: str, workspace_id: str
    ) -> None:
        """Facade → TrustGate.record_auto_execution_outcome."""
        await self._trust_gate.record_auto_execution_outcome(capability, risk_level, workspace_id)

    def _remember_auto_executed(self, run: TaskRun, capability: str, risk_level: str) -> None:
        """Facade → TrustGate.remember_auto_executed."""
        self._trust_gate.remember_auto_executed(run, capability, risk_level)

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
        """Facade → StepRunner.run_step_action."""
        return await self._runner.run_step_action(step, run, cancel_event=cancel_event)

    async def _run_step_via_agent_loop(
        self,
        step: TaskStep,
        run: TaskRun,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """Facade → StepRunner.run_step_via_agent_loop."""
        return await self._runner.run_step_via_agent_loop(step, run, cancel_event=cancel_event)

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

    async def _get_ready_steps(self, run_id: str) -> list[TaskStep]:
        """Facade → StepGraphStore.get_ready_steps."""
        return await self._store.get_ready_steps(run_id)

    async def _get_all_steps(self, run_id: str) -> list[TaskStep]:
        """Facade → StepGraphStore.get_all_steps."""
        return await self._store.get_all_steps(run_id)

    async def _resolve_step_references(self, step: TaskStep, run_id: str) -> dict:
        """Facade → StepGraphStore.resolve_step_references."""
        return await self._store.resolve_step_references(step, run_id)

    async def _checkpoint(self, run: TaskRun, step_id: str | None, reason: str) -> None:
        """Facade → StepGraphStore.checkpoint."""
        await self._store.checkpoint(run, step_id, reason)

    async def _writeback_memories(self, run: TaskRun) -> None:
        """Facade → OutcomeLearner.writeback_memories."""
        await self._learner.writeback_memories(run)

    def _spawn_background(self, coro) -> None:
        """Run a best-effort coroutine fire-and-forget, tracked so it isn't GC'd
        before completion. Used for learning that must not hold the run's session."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _learn_entities_isolated(
        self, source_text: str, user_id: str, workspace_id: str, run_id: str
    ) -> None:
        """Facade → OutcomeLearner.learn_entities_isolated."""
        await self._learner.learn_entities_isolated(source_text, user_id, workspace_id, run_id)

    async def _run_verification(self, run: TaskRun) -> None:
        """Facade → OutcomeLearner.run_verification."""
        await self._learner.run_verification(run)

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
        """Facade → StepGraphStore.build_graph_definition."""
        return StepGraphStore.build_graph_definition(tasks)
