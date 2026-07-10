"""Background / approval-resume TaskRun execution with retry + DLQ."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.errors import classify, new_correlation_id
from src.middleware.observability import get_correlation_id

logger = logging.getLogger(__name__)


# Log the degraded "no orchestrator" state every Nth tick so it is observable
# without flooding the log on every 30s cycle.
_NO_ORCH_LOG_EVERY = 20


class BackgroundTasksTickMixin:
    """Executes pending background TaskRuns, retrying then dead-lettering."""

    async def _tick_background_tasks(self, factory) -> None:
        """Execute pending background tasks queued by the orchestrator.

        Picks up TaskRuns with source in ("background", "approval_resume")
        and status="pending". Failed tasks are retried up to max_retries,
        then moved to the dead-letter queue.
        """
        if not self._orchestrator:
            # Degraded: the worker's orchestrator failed to build, so the
            # background + approval-resume execution path is dead. This is a
            # P0 silent no-op if left unlogged — surface it loudly (throttled)
            # so /health and operators can see the worker is half-broken.
            self._no_orch_ticks = getattr(self, "_no_orch_ticks", 0) + 1
            if self._no_orch_ticks % _NO_ORCH_LOG_EVERY == 1:
                logger.error(
                    "Scheduler background tick has NO orchestrator — background "
                    "tasks and approval-resume runs cannot execute (degraded). "
                    "Consecutive degraded ticks: %d",
                    self._no_orch_ticks,
                )
            return

        try:
            from src.models.task_graph import TaskRun, TaskStep
            from src.services.execution_state import transition_run

            async with factory() as db:
                from sqlalchemy import or_

                result = await db.execute(
                    select(TaskRun)
                    .where(
                        or_(
                            # Fresh background runs
                            (TaskRun.status == "pending")
                            & TaskRun.source.in_(["background", "approval_resume"]),
                            # Runs approved by user, awaiting scheduler resume
                            (TaskRun.status == "awaiting_approval")
                            & (TaskRun.source == "approval_resume"),
                        ),
                    )
                    .order_by(TaskRun.created_at.asc())
                    .limit(3)
                    .with_for_update(skip_locked=True)
                )
                pending = list(result.scalars().all())

                if not pending:
                    return

                for run in pending:
                    # Capture IDs before execution — if the session enters
                    # PendingRollbackError state, lazy attribute access fails.
                    run_id = run.run_id
                    run_status = run.status
                    plan_id = run.plan_id
                    user_id = run.user_id
                    ws_id = run.workspace_id or ""

                    try:
                        from src.services.graph_executor import (
                            create_graph_executor,
                        )

                        executor = await create_graph_executor(
                            settings=self._settings,
                            db=db,
                            workspace_id=ws_id,
                            db_factory=factory,
                            execute_tool_fn=self._orchestrator._execute_tool,
                            budget=self._orchestrator._budget,
                            circuit_breaker=getattr(self._orchestrator, "_circuit_breaker", None),
                            # Step 10C P2: durable autonomous deep step-executor. Defensively
                            # resolved (None when the invoker/method is unavailable). Byte-neutral
                            # — the per-surface effective-runtime gate is off by default, so
                            # StepRunner.run_step_action never routes to it.
                            deep_step_runner=getattr(
                                getattr(self._orchestrator, "_invoker", None),
                                "run_autonomous_deep_step",
                                None,
                            ),
                        )

                        if run_status == "awaiting_approval":
                            # Approval-resumed: use resume_run to recover
                            # checkpoint, surface_id, and stale-context refresh.
                            # Reset source so retries (failed → pending) go
                            # through execute_run, not resume_run.
                            run.source = "background"
                            await db.flush()
                            completed = await executor.resume_run(run_id)
                            logger.info(
                                "Approval-resumed task %s completed: %s",
                                run_id,
                                completed.status,
                            )
                        else:
                            # Fresh background run: ensure steps exist, then execute.
                            step_check = await db.execute(
                                select(TaskStep.step_id).where(TaskStep.run_id == run_id).limit(1)
                            )
                            if not step_check.scalar_one_or_none() and plan_id:
                                await executor.populate_run_steps(run_id, plan_id)
                                await db.flush()

                            # Generate trace_id so execution is observable
                            from ulid import ULID

                            bg_trace_id = f"trace_{ULID()}"
                            completed = await executor.execute_run(run_id, trace_id=bg_trace_id)
                            logger.info(
                                "Background task %s completed: %s",
                                run_id,
                                completed.status,
                            )
                    except Exception as e:
                        # Rollback poisoned session before any further DB access.
                        await db.rollback()

                        # rollback() expires ALL ORM instances (independent of
                        # expire_on_commit). Reading run.* on the now-expired
                        # `run` would trigger implicit lazy IO and raise
                        # MissingGreenlet under async SQLAlchemy — silently
                        # losing the retry bookkeeping. Re-fetch a fresh,
                        # attached instance inside the async context first.
                        refetched = await db.get(TaskRun, run_id)
                        if refetched is not None:
                            run = refetched

                        logger.warning(
                            "Background task %s failed: %s",
                            run_id,
                            e,
                        )
                        run.retry_count = (run.retry_count or 0) + 1
                        max_retries = run.max_retries or 3

                        if run.retry_count >= max_retries:
                            # Exhausted retries — mark failed and DLQ
                            try:
                                transition_run(run, "failed")
                            except Exception:
                                run.status = "failed"
                            # run.error is surfaced in execution surfaces +
                            # run history (client-facing) — store the safe
                            # message + code + correlation id, never str(e).
                            _code, _msg, _ = classify(e)
                            run.error = {
                                "type": "execution_error",
                                "message": _msg,
                                "error_code": _code,
                                "correlation_id": get_correlation_id() or new_correlation_id(),
                            }
                            run.completed_at = datetime.now(timezone.utc)
                            try:
                                from src.services.dead_letter import (
                                    DeadLetterService,
                                )

                                dlq = DeadLetterService(db)
                                await dlq.enqueue(
                                    user_id=user_id,
                                    operation_type="background_task",
                                    error_type=type(e).__name__,
                                    # DLQ error_message is internal-only (DLQ
                                    # stats expose counts, not text) — raw str(e)
                                    # is fine here and aids debugging/retry.
                                    error_message=str(e),
                                    source_id=run_id,
                                    payload={
                                        "plan_id": plan_id,
                                        "run_id": run_id,
                                    },
                                    workspace_id=ws_id,
                                )
                            except Exception:
                                logger.debug(
                                    "DLQ enqueue failed for run %s",
                                    run_id,
                                    exc_info=True,
                                )
                        else:
                            # Retry: transition back to pending
                            if run.status not in ("pending", "failed"):
                                try:
                                    transition_run(run, "failed")
                                except Exception:
                                    run.status = "failed"
                            try:
                                transition_run(run, "pending")
                            except Exception:
                                run.status = "pending"
                            logger.info(
                                "Background task %s retry %d/%d",
                                run_id,
                                run.retry_count,
                                max_retries,
                            )

                await db.commit()
                logger.info(
                    "Background tick: %d tasks processed",
                    len(pending),
                )
        except Exception:
            logger.warning("Background task tick error", exc_info=True)
