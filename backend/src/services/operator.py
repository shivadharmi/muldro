"""Operator — orchestrates plan execution via GraphExecutor.

Thin wrapper that delegates to GraphExecutor for DAG-based execution
with checkpoints, parallel steps, and approval gates.

Responsibilities:
- Bridge between TaskRun records and the graph executor
- Track execution state machine (task runs, artifacts)
- Report status back for presentation
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.plans import Plan
from src.models.task_graph import TaskRun
from src.services.audit import AuditService

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.services.graph_executor import GraphExecutor

logger = logging.getLogger(__name__)


class Operator:
    """Execute approved plans — always delegates to GraphExecutor."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        notifier=None,
        graph_executor: GraphExecutor | None = None,
    ):
        self._settings = settings
        self._db = db
        self._audit = AuditService(db)
        self._notifier = notifier
        self._graph_executor = graph_executor

    async def execute_plan(self, run_id: str, user_id: str) -> bool:
        """Execute all tasks in a plan via GraphExecutor. Returns True on success."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            logger.error("TaskRun not found: %s", run_id)
            return False

        if not run.plan_id:
            logger.error("TaskRun %s has no plan_id — cannot execute", run_id)
            return False

        result = await self._db.execute(select(Plan).where(Plan.plan_id == run.plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            logger.error("Plan not found: %s", run.plan_id)
            return False

        if not self._graph_executor:
            logger.error("GraphExecutor not available — cannot execute plan %s", plan.plan_id)
            run.status = "failed"
            run.error = {"message": "GraphExecutor unavailable"}
            await self._db.commit()
            return False

        return await self._execute_via_graph(run, plan, user_id)

    async def _execute_via_graph(self, run: TaskRun, plan: Plan, user_id: str) -> bool:
        """Execute using the DAG-based graph executor."""
        try:
            # The governor already created the TaskRun — the graph executor
            # populates it with steps and runs the DAG.
            await self._graph_executor.populate_run_steps(run.run_id, plan.plan_id)

            run.status = "running"
            await self._db.flush()

            await self._audit.log(
                user_id=user_id,
                action_type="execution_started",
                plan_id=plan.plan_id,
                execution_id=run.run_id,
                summary=f"Executing plan via graph: {plan.goal}",
                workspace_id=run.workspace_id,
            )

            completed_run = await self._graph_executor.execute_run(run.run_id)
            success = completed_run.status == "completed"

            plan.status = "completed" if success else "failed"

            await self._audit.log(
                user_id=user_id,
                action_type="execution_completed" if success else "execution_failed",
                plan_id=plan.plan_id,
                execution_id=run.run_id,
                summary=f"Plan '{plan.goal}' {'completed' if success else 'failed'}",
                workspace_id=run.workspace_id,
            )

            await self._db.commit()
            await self._notify_completion(run, plan, user_id, success)

            logger.info(
                "Run %s %s for plan %s",
                run.run_id,
                "completed" if success else "failed",
                plan.plan_id,
            )
            return success

        except Exception as exc:
            logger.error("Graph execution failed for plan %s: %s", plan.plan_id, exc)
            run.status = "failed"
            run.error = {"message": str(exc)[:500]}
            plan.status = "failed"
            await self._db.commit()
            return False

    async def _notify_completion(
        self, run: TaskRun, plan: Plan, user_id: str, success: bool
    ) -> None:
        """Notify user about execution completion."""
        if self._notifier:
            try:
                status = "completed" if success else "failed"
                await self._notifier.notify(
                    user_id=user_id,
                    notification_type="info_update",
                    title=f"Task {status}: {plan.goal}",
                    body=f"Run {run.run_id} {status}.",
                    data={"plan_id": plan.plan_id, "run_id": run.run_id},
                    workspace_id=run.workspace_id,
                )
            except Exception:
                logger.warning("Failed to notify for execution", exc_info=True)
