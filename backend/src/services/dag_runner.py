"""DagRunner — the DAG execution engine (loop + per-step pipeline).

Extracted from ``GraphExecutor`` (god-object decomposition, 2026-06-20). This is
the mediator that drives a run's step DAG to completion: the ready-step loop
(``execute_dag``), the per-step pipeline with the single TrustEngine approval gate
(``execute_step``), step finalization (``finalize_step``), and retry/permanent
failure handling (``handle_step_failure``).

It orchestrates the other collaborators and depends *downward* on them —
``StepGraphStore`` (step queries, references, checkpoint), ``StepRunner`` (the
agent loop), ``TrustGate`` (risk/approval helpers), ``OutcomeLearner`` (verification
+ memory writeback), and ``SurfaceEmitter`` (events + surface updates) — plus the
``TrustEngine`` for the gate decision. It never imports ``graph_executor``; the
coordinator owns run lifecycle (trace, timeout, commit) and calls ``execute_dag``.

Status changes go through ``transition_run``/``transition_step`` (never direct
mutation). The single TrustEngine gate (4×4 matrix; fail-closed contract guard on
missing engine/empty capability) is preserved verbatim.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.contracts import ResultSummary, StepResult
from src.integrations.mcp_errors import McpAuthRequiredError
from src.integrations.provider_map import provider_for_server
from src.models.task_graph import TaskRun, TaskStep
from src.orchestrator.agent_loop import CancellationRequested
from src.services.execution_state import TERMINAL_SUCCESS, transition_run, transition_step
from src.services.execution_support import (
    _compute_retry_delay,
    _detect_auth_required,
    _safe_error_fields,
    _step_to_state,
)
from src.services.execution_surface_emitter import SurfaceEmitter
from src.services.outcome_learner import OutcomeLearner
from src.services.step_graph_store import StepGraphStore
from src.services.step_runner import StepRunner
from src.services.trust_gate import TrustGate

if TYPE_CHECKING:
    from src.services.verification import VerifyVerdict

logger = logging.getLogger(__name__)


def build_verification_meta(capability: str, risk, verdict, output: dict | None) -> dict:
    """Metadata attached to a verified write's output_data so the deferred-read tick
    and escalation can act on a completed_unverified / partially_completed step
    without re-deriving risk. artifact_ref carries the exact observed effect."""
    out = output if isinstance(output, dict) else {}
    return {
        "capability": capability,
        "risk_level": getattr(risk, "risk_level", "high"),
        "reversible": getattr(risk, "reversible", False),
        "blast_radius": getattr(risk, "blast_radius", "self"),
        "verdict": verdict.value if hasattr(verdict, "value") else str(verdict),
        "attempts": 1,
        "artifact_ref": {
            k: out.get(k)
            for k in ("event_id", "id", "message_id", "url", "thread_id")
            if out.get(k) is not None
        },
    }


class DagRunner:
    """Drives a run's step DAG: ready-step loop, gate, execution, finalization."""

    def __init__(
        self,
        *,
        db,
        store: StepGraphStore,
        trust_gate: TrustGate,
        runner: StepRunner,
        learner: OutcomeLearner,
        emitter: SurfaceEmitter,
        trust_engine_provider,
        reauth_service_provider=None,
    ):
        self._db = db
        self._store = store
        self._trust_gate = trust_gate
        self._runner = runner
        self._learner = learner
        self._emitter = emitter
        # Resolved live via a provider so the coordinator stays the single source
        # of truth (tests reassign executor._trust_engine after construction).
        self._trust_engine_provider = trust_engine_provider
        # ReauthService is resolved live via a provider for the same reason
        # (tests reassign executor._reauth_service after construction). It may be
        # absent (None) when re-auth wiring is unavailable — auth_required is then
        # treated as a normal step failure rather than a deferral.
        self._reauth_service_provider = reauth_service_provider

    @property
    def _trust_engine(self):
        """Resolve the current TrustEngine live via the provider."""
        return self._trust_engine_provider()

    @property
    def _reauth_service(self):
        """Resolve the current ReauthService live via the provider (may be None)."""
        if self._reauth_service_provider is None:
            return None
        return self._reauth_service_provider()

    async def execute_dag(
        self,
        run: TaskRun,
        surface_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """Main DAG execution loop."""
        _dag_start = time.monotonic()
        while True:
            ready_steps = await self._store.get_ready_steps(run.run_id)
            if not ready_steps:
                # Check if all steps are done
                all_steps = await self._store.get_all_steps(run.run_id)
                pending = [s for s in all_steps if s.status in ("pending", "ready", "running")]
                if not pending:
                    # Use partially_completed before verification (if verifier exists)
                    if self._learner.verification_enabled:
                        transition_run(run, "partially_completed")
                    else:
                        transition_run(run, "completed")
                    run.completed_at = datetime.now(timezone.utc)
                    await self._emitter.emit_event(
                        "run_completed",
                        run.user_id,
                        {"run_id": run.run_id, "plan_id": run.plan_id},
                        workspace_id=run.workspace_id,
                    )
                    # Run verifier if available
                    if self._learner.verification_enabled:
                        await self._learner.run_verification(run)
                    # Writeback memories from execution results
                    await self._learner.writeback_memories(run)
                    if surface_id:
                        _comp_steps = await self._store.get_all_steps(run.run_id)
                        _final_states = [_step_to_state(s) for s in _comp_steps]
                        _findings = [
                            str(s.output_data.get("result", ""))
                            for s in _comp_steps
                            if s.output_data and s.output_data.get("result")
                        ]
                        await self._emitter.emit_surface_update(
                            surface_id=surface_id,
                            user_id=run.user_id,
                            phase="completed",
                            steps=_final_states,
                            progress=f"{len(_comp_steps)}/{len(_comp_steps)} steps",
                            results=ResultSummary(key_findings=_findings[:5]),
                            workspace_id=run.workspace_id,
                            tokens=((run.input_tokens or 0) + (run.output_tokens or 0)) or None,
                            cost_usd=run.cost_usd if run.cost_usd else None,
                        )
                        # Emit a lightweight summary card for the workspace
                        # feed and archive the run surface.
                        await self._emitter.emit_summary_surface(run, surface_id)
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
                        _fail_steps = await self._store.get_all_steps(run.run_id)
                        _fail_states = [_step_to_state(s) for s in _fail_steps]
                        await self._emitter.emit_surface_update(
                            surface_id=surface_id,
                            user_id=run.user_id,
                            phase="failed",
                            steps=_fail_states,
                            progress=f"{len(failed)} step(s) failed",
                            workspace_id=run.workspace_id,
                        )
                        await self._emitter.emit_summary_surface(run, surface_id)
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
                _all_for_surface = await self._store.get_all_steps(run.run_id)
                _step_states = [
                    _step_to_state(
                        s,
                        status_override="executing"
                        if s.step_id in (run.current_step_ids or [])
                        else None,
                    )
                    for s in _all_for_surface
                ]
                _done_count = sum(1 for s in _all_for_surface if s.status in TERMINAL_SUCCESS)
                await self._emitter.emit_surface_update(
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
                    await self.execute_step(
                        run, step, surface_id=surface_id, cancel_event=cancel_event
                    )
                except CancellationRequested:
                    # Run was cancelled — cancel_run() already set the run status
                    return
                except Exception:
                    logger.error("Step %s raised unexpectedly", step.step_id, exc_info=True)

                # Stop dispatching the rest of this ready batch the moment a step
                # pauses OR defers the run (approval gate / awaiting input /
                # awaiting_reauth). transition_run mutates run.status in place, so
                # this reflects the pause without a refresh. Without including
                # ``awaiting_reauth`` here, the step the defer left ``running``
                # (which get_ready_steps still returns so resumed runs re-pick it)
                # would be re-executed → re-deferred → invalid awaiting_reauth →
                # awaiting_reauth transition (swallowed) → churn until the subtick
                # timeout. The deferred step is re-evaluated when the run is
                # re-queued after reconnect.
                if run.status in (
                    "paused",
                    "awaiting_approval",
                    "awaiting_input",
                    "awaiting_reauth",
                ):
                    break

            # Check if run was paused by an approval gate or deferred for re-auth.
            # NOTE: refresh would clobber the in-memory awaiting_reauth status from
            # the DB (the defer is not committed yet on this coordinator session),
            # so only refresh when the run is NOT already parked in-memory.
            if run.status not in ("paused", "awaiting_approval", "awaiting_reauth"):
                await self._db.refresh(run)
            if run.status in ("paused", "awaiting_approval", "awaiting_reauth"):
                break

        _dag_elapsed = time.monotonic() - _dag_start
        if _dag_elapsed > 120:
            logger.warning(
                "Long DAG execution: run %s took %.1fs — "
                "consider db_factory pattern for connection pool safety",
                run.run_id,
                _dag_elapsed,
            )

    async def execute_step(
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
                await self._emitter.emit_event(
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
            risk = await self._trust_gate.assess_step_risk(capability, step, run)
            decision = await self._trust_engine.evaluate(
                capability, risk, workspace_id=run.workspace_id or ""
            )

            if decision.decision == "approval_required":
                await self._trust_gate.create_approval_and_pause(
                    run, step, capability, risk, decision, surface_id=surface_id
                )
                return

            # auto_execute_notify or auto_execute_silent — proceed
            transition_step(step, "running")
            step.started_at = step.started_at or datetime.now(timezone.utc)
            await self._db.flush()
            await self._emitter.emit_event(
                "step.started",
                run.user_id,
                {"run_id": run.run_id, "step_id": step.step_id},
                workspace_id=run.workspace_id,
            )

            resolved_input = await self._store.resolve_step_references(step, run.run_id)
            if resolved_input != (step.input_data or {}):
                step.input_data = resolved_input
                await self._db.flush()

            step_timeout = step.timeout_seconds or 120
            t0 = time.monotonic()
            try:
                output = await asyncio.wait_for(
                    self._runner.run_step_action(step, run, cancel_event=cancel_event),
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
            except McpAuthRequiredError as exc:
                if await self._defer_for_reauth(run, step, exc, surface_id=surface_id):
                    return
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                await self.handle_step_failure(run, step, exc, elapsed_ms, surface_id=surface_id)
                return
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                await self.handle_step_failure(run, step, exc, elapsed_ms, surface_id=surface_id)
                return

            # A tool that hit a permanent OAuth failure surfaces an auth_required
            # signal in the step output. Defer the whole run for re-authorization
            # instead of failing it (it is re-queued on reconnect).
            if await self._defer_for_reauth(run, step, output, surface_id=surface_id):
                return

            if decision.decision == "auto_execute_notify":
                await self._trust_gate.notify_auto_executed(run, step, risk, output)

            # Read-back verification BEFORE marking terminal (spec §4.5), shared with
            # the approved-resume path so NO write path emits a terminal status without
            # a verdict. Only irreversible writes are verified; everything else is
            # trivially CONFIRMED.
            from src.services.verification import VerifyVerdict

            verdict = await self._finalize_with_verification(
                run, step, output, elapsed_ms, capability, risk
            )

            # Trust reinforcement fires ONLY on a confirmed-verified completion, and
            # ONLY on the auto-execute path (the approved-resume path records trust at
            # approval time in routes_approvals). Task 7 moves the completed_unverified
            # deferred-increment to the tick.
            risk_level = getattr(risk, "risk_level", risk)
            if verdict == VerifyVerdict.CONFIRMED:
                await self._trust_gate.record_auto_execution_outcome(
                    capability, risk_level, run.workspace_id or ""
                )
            # Record the auto-executed (capability, risk_level) regardless, so the
            # deferred-read tick can fire the increment when a completed_unverified
            # step is later confirmed (Task 7/9). Now finally has a reader.
            self._trust_gate.remember_auto_executed(run, capability, risk_level)
            return

        # ── Common execution path (step resumed after approval) ──────
        step.started_at = step.started_at or datetime.now(timezone.utc)
        await self._db.flush()
        await self._emitter.emit_event(
            "step.started",
            run.user_id,
            {"run_id": run.run_id, "step_id": step.step_id},
            workspace_id=run.workspace_id,
        )

        resolved_input = await self._store.resolve_step_references(step, run.run_id)
        if resolved_input != (step.input_data or {}):
            step.input_data = resolved_input
            await self._db.flush()

        step_timeout = step.timeout_seconds or 120
        t0 = time.monotonic()
        try:
            output = await asyncio.wait_for(
                self._runner.run_step_action(step, run, cancel_event=cancel_event),
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
        except McpAuthRequiredError as exc:
            if await self._defer_for_reauth(run, step, exc, surface_id=surface_id):
                return
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            await self.handle_step_failure(run, step, exc, elapsed_ms, surface_id=surface_id)
            return
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            await self.handle_step_failure(run, step, exc, elapsed_ms, surface_id=surface_id)
            return

        if await self._defer_for_reauth(run, step, output, surface_id=surface_id):
            return

        # Approved-resume path: a HUMAN-approved write is the highest-risk write class,
        # so it must be read-back verified too (spec §4.5 — "ANY write path"). Re-derive
        # the verifier inputs: capability from the step, risk via assess_step_risk (the
        # RiskAssessor is Redis-cached 24h, and this capability was already assessed at
        # pause time, so a resume within the window is a cache hit — cheap). Trust for
        # the approved path is recorded at approval time (routes_approvals), NOT here.
        capability = (step.input_data or {}).get(
            "capability", (step.input_data or {}).get("task_type", "unknown")
        )
        risk = await self._trust_gate.assess_step_risk(capability, step, run)
        await self._finalize_with_verification(run, step, output, elapsed_ms, capability, risk)

    async def _finalize_with_verification(
        self,
        run: TaskRun,
        step: TaskStep,
        output: dict | None,
        elapsed_ms: int,
        capability: str,
        risk,
    ) -> "VerifyVerdict":
        """Read-back verify a write step BEFORE marking it terminal, then finalize with
        the verdict's status (spec §4.5). Shared by the auto-execute and approved-resume
        paths so neither can emit a terminal status without a verdict. A CONTRADICTED
        irreversible write escalates to the user (escalate-first — wired in Task 8).
        Returns the verdict; the caller owns any path-specific trust bookkeeping (this
        helper deliberately does NOT touch trust)."""
        from src.services.verification import ReadBackVerifier, VerifyVerdict
        from src.services.verification.readback import verdict_to_step_status

        verifier = ReadBackVerifier(
            read_fn=lambda cap, args: self._runner.run_readback(cap, args, run)
        )
        verdict = await verifier.verify_step(
            capability=capability,
            write_input=step.input_data or {},
            write_output=output if isinstance(output, dict) else {},
            risk=risk,
        )
        step_status = verdict_to_step_status(verdict)

        # Attach verification metadata so the deferred tick / escalation can act
        # on a completed_unverified / partially_completed step (JSONB, no migration).
        # A CONFIRMED write finalizes as "completed" and needs no deferred re-check.
        if step_status != "completed" and isinstance(output, dict):
            output = {
                **output,
                "verification": build_verification_meta(capability, risk, verdict, output),
            }

        await self.finalize_step(run, step, output, elapsed_ms, status=step_status)

        if verdict == VerifyVerdict.CONTRADICTED:
            await self._escalate_divergence(run, step, capability, risk, output)
        return verdict

    async def _defer_for_reauth(
        self,
        run: TaskRun,
        step: TaskStep,
        result,
        surface_id: str | None = None,
    ) -> bool:
        """Park the run for OAuth re-authorization on an ``auth_required`` signal.

        ``result`` is either a step-output dict (inspected for the structured
        ``auth_required`` envelope) or a surfaced ``McpAuthRequiredError``. When
        an auth requirement is detected and a ReauthService is wired:

        - the run is transitioned to ``awaiting_reauth`` (via
          ``ReauthService.defer_run`` → state machine) with the blocking
          provider stored in ``run.checkpoint["awaiting_provider"]``;
        - the integration is flagged ``needs_reauth`` and the provider's
          perception sources are paused (``apply_needs_reauth``).

        The deferred step is left ``running``; ``StepGraphStore.get_ready_steps``
        re-picks running steps, so the step re-executes once the run is re-queued
        after reconnect. (We deliberately do NOT reset it to ``ready`` — that is
        not a valid step transition and the running→resume path already covers
        re-execution.)

        ATOMICITY (C4): every DB write — the run defer (run → awaiting_reauth),
        the integration status flip, and the source pause — happens on the
        COORDINATOR session ``self._db`` and is NOT committed here. The
        graph_executor coordinator owns the single commit (``execute_run`` /
        ``resume_run`` commit ``self._db`` after the DAG returns), so the
        run-defer + status-flip + source-pause land in ONE transaction. A failure
        of that commit rolls back all three together — no orphan where the
        integration is flagged but the run was never deferred. ``notify_reauth``
        is external (Redis dedup + Notifier), idempotent, and safe to fire here.

        Returns ``True`` when the run was deferred (caller must stop and NOT
        finalize/fail the step), ``False`` otherwise.
        """
        reauth = self._reauth_service
        if reauth is None:
            return False

        provider = ""
        if isinstance(result, McpAuthRequiredError):
            provider = result.provider or provider_for_server(result.server or "")
        else:
            auth = _detect_auth_required(result)
            if not auth:
                return False
            provider = auth.get("provider") or provider_for_server(auth.get("server", ""))

        if not provider:
            return False

        # All defer writes on the COORDINATOR session — no separate session, no
        # premature commit (see ATOMICITY above):
        #   1. park the run (running → awaiting_reauth) + record the provider;
        #   2. flag the integration needs_reauth + pause its perception sources.
        await reauth.defer_run(self._db, run, provider)
        await reauth.apply_needs_reauth(self._db, run.user_id, provider, "auth_required")
        await self._db.flush()

        # Notify the user (external: Redis dedup + Notifier). Idempotent and not a
        # DB write on self._db, so it is safe to fire before the coordinator commit.
        try:
            await reauth.notify_reauth(
                run.user_id,
                provider,
                "auth_required",
                workspace_id=run.workspace_id or "",
            )
        except Exception:
            logger.warning(
                "notify_reauth failed for %s/%s (run deferred regardless)",
                run.user_id,
                provider,
                exc_info=True,
            )

        await self._emitter.emit_event(
            "run.awaiting_reauth",
            run.user_id,
            {"run_id": run.run_id, "step_id": step.step_id, "provider": provider},
            workspace_id=run.workspace_id,
        )
        logger.info(
            "Run %s deferred for re-auth (provider=%s, step=%s)",
            run.run_id,
            provider,
            step.step_id,
        )
        return True

    async def handle_step_failure(
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
            await self._emitter.emit_event(
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
                all_steps = await self._store.get_all_steps(run.run_id)
                step_states = [
                    _step_to_state(
                        s,
                        status_override="failed" if s.step_id == step.step_id else None,
                    )
                    for s in all_steps
                ]
                await self._emitter.emit_surface_update(
                    surface_id=surface_id,
                    user_id=run.user_id,
                    phase="failed",
                    steps=step_states,
                    progress=f"Step {step.step_id} permanently failed",
                    workspace_id=run.workspace_id,
                )
        await self._db.flush()

    async def finalize_step(
        self,
        run: TaskRun,
        step: TaskStep,
        output: dict | None,
        elapsed_ms: int,
        status: str = "completed",
    ) -> None:
        """Mark step terminal (status defaults to completed; a write step passes the
        read-back verdict status — completed / completed_unverified / partially_completed).
        Emit events, checkpoint."""
        await self._emitter.emit_event(
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

        transition_step(step, status)
        step.output_data = output
        step.completed_at = datetime.now(timezone.utc)
        await self._db.flush()

        result = StepResult(
            step_id=step.step_id,
            status=status,
            output_data=output,
            duration_ms=elapsed_ms,
        )

        await self._store.checkpoint(run, step.step_id, "step_completed")

        await self._emitter.emit_event(
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
            await self._emitter.emit_event(
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

    async def _escalate_divergence(self, run, step, capability, risk, output) -> None:
        """Escalate-first compensation for a CONTRADICTED read-back on an irreversible
        write (spec §7). Temporary no-op stub — Task 8 replaces the body with the actual
        user-surfacing escalation. The explicit 5-arg signature documents the Task 8
        contract (and catches call-site drift). Kept here so the execute_step wiring is
        self-consistent and green."""
        return None
