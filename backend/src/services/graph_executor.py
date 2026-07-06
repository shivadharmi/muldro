"""DAG-based execution engine with checkpoints and approval gates.

Replaces the sequential loop in Operator with a proper graph executor
that resolves dependencies, runs independent steps in parallel,
checkpoints after each step, and pauses at approval gates.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.connectors.mcp_bridge import close_turn_sessions
from src.contracts import PolicyDecision
from src.integrations.turn_scope import turn_scope
from src.models.ids import ensure_prefix
from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskRun, TaskStep
from src.orchestrator.tracing import JarvisTrace
from src.services.audit import AuditService
from src.services.dag_runner import DagRunner
from src.services.execution_state import (
    RUN_TRANSITIONS,
    TERMINAL_SUCCESS,
    transition_run,
    transition_step,
)
from src.services.execution_support import _safe_error_fields, _step_to_state
from src.services.execution_surface_emitter import SurfaceEmitter

# Re-exported so callers/tests using `from src.services.graph_executor import
# create_graph_executor` (and patching it here) keep working after the factory
# moved to graph_executor_factory.py. The factory imports GraphExecutor lazily,
# so this import is acyclic.
from src.services.graph_executor_factory import create_graph_executor  # noqa: F401
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
        # OAuth re-auth coordination. When a step hits a permanent OAuth failure
        # (auth_required), the DagRunner parks the run in awaiting_reauth and
        # prompts the user via this service rather than failing the run. Built
        # from existing collaborators (db_factory + notifier + redis); resolved
        # live by the DagRunner via a provider so tests can reassign it.
        self._reauth_service = None
        if db_factory is not None and notifier is not None:
            from src.services.reauth_service import ReauthService

            self._reauth_service = ReauthService(
                db_factory=db_factory,
                notifier=notifier,
                redis=redis,
                settings=settings,
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
        # The DAG execution engine (ready-step loop + per-step pipeline + single
        # TrustEngine gate) lives in an injected collaborator that orchestrates
        # all the above. The coordinator owns run lifecycle (trace/timeout/commit)
        # and delegates the engine to it; thin facades preserve the white-box suite.
        self._dag_runner = DagRunner(
            db=db,
            store=self._store,
            trust_gate=self._trust_gate,
            runner=self._runner,
            learner=self._learner,
            emitter=self._surface_emitter,
            trust_engine_provider=lambda: self._trust_engine,
            reauth_service_provider=lambda: self._reauth_service,
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
                # run_id is already ``run_<ULID>``; the canonical run surface id
                # IS the run_id (re-prefixing produced the doubled ``run_run_…``).
                surface_id = ensure_prefix("run", run_id)
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
                # On a pause (awaiting_approval / awaiting_input / paused), roll the
                # partial segment trace up onto the run row so steps executed BEFORE
                # the pause are reflected and a Trace row exists for the surface to
                # read. _finalize_trace in the finally below ALSO fires on every exit
                # (and additionally pops/finishes the trace); it rolls the SAME
                # trace_id, so this checkpoint is an idempotent overwrite, never a
                # double count (see _roll_trace_onto_run). The explicit call keeps the
                # pause-time rollup intent clear and independent of finally ordering.
                if run.status in ("awaiting_approval", "awaiting_input", "paused"):
                    await self._checkpoint_trace(run)
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
                # A durable state-recording event flush inside the DAG (§4.8,
                # SurfaceEmitter.emit_event(durable=True)) can transiently fail and
                # deactivate the shared session ("partial rollback" state); the tail commit
                # would then raise PendingRollbackError. session.is_active only reflects this
                # AFTER a flush is attempted against the aborted transaction — an ordinary
                # Python-level failure never touches the DB, so is_active reads True until we
                # actually try one. Mark failed in memory, then flush to both (a) persist the
                # common case and (b) probe for poisoning. ONLY when that flush fails do we
                # roll back + re-hydrate: rollback() expires ORM state AND reverts the
                # flushed-but-uncommitted "running" transition, so re-establish an in-flight
                # status before re-marking failed (the machine forbids e.g. pending→failed).
                # A healthy session's flush succeeds here and the tail commit is then a cheap
                # no-op re-flush, preserving the run's partial step progress + trace (the DAG
                # never commits mid-run).
                transition_run(run, "failed")
                run.completed_at = datetime.now(timezone.utc)
                # run.error is rendered in execution surfaces + run history (client-facing).
                # Store the safe message + code + correlation id; raw str(exc) → logs only.
                safe = _safe_error_fields(exc)
                run.error = {"type": "execution_error", **safe}
                try:
                    await self._db.flush()
                except Exception:
                    logger.debug(
                        "Mark-failed probe flush failed; session likely poisoned", exc_info=True
                    )
                if not self._db.is_active:
                    await self._db.rollback()
                    await self._db.refresh(run)
                    if "failed" not in RUN_TRANSITIONS.get(run.status, set()):
                        transition_run(run, "running")
                    transition_run(run, "failed")
                    run.completed_at = datetime.now(timezone.utc)
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
                from src.services.run_detail_store import RunDetailStore

                _prior = await RunDetailStore(self._db).get_context_pack(run.run_id)
                if _prior is None:
                    _prior = {}
                fresh_pack = await self._context_builder.build(
                    user_id=run.user_id,
                    query=_prior.get("task_summary", "")[:500],
                    workspace_id=run.workspace_id,
                )
                await RunDetailStore(self._db).upsert_context_pack(
                    run.run_id, run.workspace_id, fresh_pack.model_dump()
                )
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
            actual_completed = {s.step_id for s in actual_steps if s.status in TERMINAL_SUCCESS}
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
            # A resumed run can pause again at the next approval gate; checkpoint
            # this segment's partial trace so its tokens accumulate onto the run
            # row even though _finalize_trace won't fire (non-terminal status).
            if run.status in ("awaiting_approval", "awaiting_input", "paused"):
                await self._checkpoint_trace(run)
        except Exception as exc:
            # Same poisoned-session recovery as execute_run (CF-2): a durable flush inside
            # the resumed DAG can deactivate the session ("partial rollback" state) and make
            # the tail commit raise PendingRollbackError. session.is_active only reflects this
            # AFTER a flush is attempted against the aborted transaction — an ordinary
            # Python-level failure never touches the DB, so is_active reads True until we
            # actually try one. Mark failed in memory, then flush to both (a) persist the
            # common case and (b) probe for poisoning. ONLY when that flush fails do we roll
            # back + re-hydrate: rollback() reverts the flushed-but-uncommitted "running", so
            # re-establish an in-flight status before re-marking failed (e.g.
            # paused→running→failed). A healthy session's flush succeeds here and the tail
            # commit is then a cheap no-op re-flush, preserving partial resume progress.
            transition_run(run, "failed")
            run.completed_at = datetime.now(timezone.utc)
            # Client-facing (served by the history API) — safe message + code only;
            # raw str(exc) goes to logs.
            safe = _safe_error_fields(exc)
            run.error = {"type": "resume_error", **safe}
            try:
                await self._db.flush()
            except Exception:
                logger.debug(
                    "Mark-failed probe flush failed; session likely poisoned", exc_info=True
                )
            if not self._db.is_active:
                await self._db.rollback()
                await self._db.refresh(run)
                if "failed" not in RUN_TRANSITIONS.get(run.status, set()):
                    transition_run(run, "running")
                transition_run(run, "failed")
                run.completed_at = datetime.now(timezone.utc)
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
        """Facade → DagRunner.execute_dag."""
        await self._dag_runner.execute_dag(run, surface_id=surface_id, cancel_event=cancel_event)

    async def _execute_step(
        self,
        run: TaskRun,
        step: TaskStep,
        surface_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """Facade → DagRunner.execute_step."""
        await self._dag_runner.execute_step(
            run, step, surface_id=surface_id, cancel_event=cancel_event
        )

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
        """Facade → DagRunner.handle_step_failure."""
        await self._dag_runner.handle_step_failure(
            run, step, exc, elapsed_ms, surface_id=surface_id
        )

    async def _finalize_step(
        self,
        run: TaskRun,
        step: TaskStep,
        output: dict | None,
        elapsed_ms: int,
    ) -> None:
        """Facade → DagRunner.finalize_step."""
        await self._dag_runner.finalize_step(run, step, output, elapsed_ms)

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

    def _roll_trace_onto_run(self, run: TaskRun, trace: JarvisTrace) -> tuple[int, int, float]:
        """Accumulate one segment's trace totals onto the run's rollup columns.

        ROLLUP INVARIANT: ``run.{input_tokens,output_tokens,cost_usd}`` always
        equal the SUM of every *distinct* segment trace's totals — never more,
        never less. A multi-segment run (each resume creates a fresh trace_id)
        therefore reflects work across all segments, while re-rolling the SAME
        trace_id is idempotent (no double-count).

        Mechanism: ``run.checkpoint['trace_rollup']`` maps ``trace_id`` → that
        trace's last-known ``{input_tokens, output_tokens, cost_usd}``. Rolling
        a trace OVERWRITES its own entry (so a later, more complete store of the
        same segment replaces the partial one rather than adding to it), then
        the run columns are recomputed as the sum over all entries. The JSONB is
        REASSIGNED (not mutated in place) so SQLAlchemy detects the change.

        This is why the pause ``_checkpoint_trace`` and terminal
        ``_finalize_trace`` cannot double-count when both fire for the same
        segment: they key on the same trace_id and the second simply replaces
        the first's entry.
        """
        input_t, output_t = trace.total_tokens()
        total_cost = 0.0
        try:
            for span in trace.spans:
                total_cost += float(getattr(span, "cost_usd", 0.0) or 0.0)
        except Exception:
            total_cost = 0.0

        rollup = dict((run.checkpoint or {}).get("trace_rollup") or {})
        rollup[trace.trace_id] = {
            "input_tokens": int(input_t or 0),
            "output_tokens": int(output_t or 0),
            "cost_usd": round(float(total_cost), 6),
        }
        sum_input = sum(int(e.get("input_tokens", 0)) for e in rollup.values())
        sum_output = sum(int(e.get("output_tokens", 0)) for e in rollup.values())
        sum_cost = sum(float(e.get("cost_usd", 0.0)) for e in rollup.values())

        # JSONB reassigned (not mutated) for SQLAlchemy change detection.
        run.checkpoint = {**(run.checkpoint or {}), "trace_rollup": rollup}
        try:
            run.input_tokens = sum_input
            run.output_tokens = sum_output
            run.cost_usd = round(sum_cost, 6)
        except Exception:
            logger.debug("Failed to roll up token usage onto run %s", run.run_id, exc_info=True)
        return sum_input, sum_output, round(sum_cost, 6)

    async def _persist_trace(self, run: TaskRun, trace: JarvisTrace) -> None:
        """Persist (upsert) a trace linked to the run. Best-effort."""
        if not self._trace_store:
            return
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

    async def _checkpoint_trace(self, run: TaskRun) -> None:
        """Roll up + persist the CURRENT segment's trace when a run PAUSES.

        Unlike ``_finalize_trace``, this does NOT pop ``_active_traces`` and does
        NOT call ``trace.finish()`` — the segment is still live (the run is only
        paused at an approval gate and may resume in-process). Leaving the entry
        in place means a subsequent ``_finalize_trace`` (terminal) or another
        ``_checkpoint_trace`` (next pause) sees the same JarvisTrace, and
        ``_roll_trace_onto_run`` keys on its trace_id so re-rolling is idempotent.
        """
        trace = self._active_traces.get(run.run_id)
        if not trace:
            return
        sum_input, sum_output, sum_cost = self._roll_trace_onto_run(run, trace)
        logger.info(
            "run_trace_checkpointed",
            extra={
                "run_id": run.run_id,
                "trace_id": trace.trace_id,
                "spans": len(trace.spans),
                "rolled_input_tokens": sum_input,
                "rolled_output_tokens": sum_output,
                "rolled_cost_usd": sum_cost,
            },
        )
        # store_trace upserts by trace_id, so persisting the partial here and the
        # complete trace later at finalize does not violate the traces PK.
        await self._persist_trace(run, trace)

    async def _finalize_trace(self, run: TaskRun) -> None:
        """Finalize and persist the JarvisTrace for a completed/failed run.

        Also writes the aggregate token/cost rollup onto the TaskRun row so
        history views can render observability metrics without joining the
        Trace table (and so that the detail endpoint has a deterministic
        non-zero result even if the trace persistence fails). The rollup
        ACCUMULATES across resume segments and is idempotent per trace_id
        (see ``_roll_trace_onto_run``), so finalizing a segment already
        checkpointed at a pause does not double-count it.
        """
        trace = self._active_traces.pop(run.run_id, None)
        if not trace:
            return
        trace.finish()
        sum_input, sum_output, sum_cost = self._roll_trace_onto_run(run, trace)

        logger.info(
            "run_trace_finalized",
            extra={
                "run_id": run.run_id,
                "trace_id": trace.trace_id,
                "spans": len(trace.spans),
                "input_tokens": sum_input,
                "output_tokens": sum_output,
                "cost_usd": sum_cost,
            },
        )
        await self._persist_trace(run, trace)

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
