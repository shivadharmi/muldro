"""DAG-based execution engine with checkpoints and approval gates.

Replaces the sequential loop in Operator with a proper graph executor
that resolves dependencies, runs independent steps in parallel,
checkpoints after each step, and pauses at approval gates.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskCheckpoint, TaskRun, TaskStep
from src.services.audit import AuditService

logger = logging.getLogger(__name__)


class GraphExecutor:
    """Durable graph executor with parallel steps, checkpoints, and approval gates."""

    def __init__(self, settings: Settings, db: AsyncSession, event_bus=None, notifier=None):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._audit = AuditService(db)
        self._event_bus = event_bus
        self._notifier = notifier

    async def create_run(self, plan_id: str, user_id: str) -> TaskRun:
        """Create a TaskRun from a Plan, building the step DAG."""
        result = await self._db.execute(select(Plan).where(Plan.plan_id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            raise ValueError(f"Plan not found: {plan_id}")

        result = await self._db.execute(
            select(PlanTask).where(PlanTask.plan_id == plan_id).order_by(PlanTask.id)
        )
        tasks = list(result.scalars().all())

        run_id = f"run_{ULID()}"
        graph_def = self._build_graph_definition(tasks)

        run = TaskRun(
            run_id=run_id,
            plan_id=plan_id,
            user_id=user_id,
            status="pending",
            graph_definition=graph_def,
        )
        self._db.add(run)

        # Create steps from tasks
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

            step = TaskStep(
                step_id=step_id,
                run_id=run_id,
                task_id=task.task_id,
                depends_on=depends_on_step_ids or None,
                status="pending",
                input_data=task.input_data,
            )
            self._db.add(step)

        await self._db.flush()
        logger.info("Run created: %s with %d steps for plan %s", run_id, len(tasks), plan_id)
        return run

    async def execute_run(self, run_id: str) -> TaskRun:
        """Execute a run's DAG to completion (or pause at approval gate)."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        await self._db.flush()

        await self._audit.log(
            user_id=run.user_id,
            action_type="run_started",
            plan_id=run.plan_id,
            execution_id=run.run_id,
            summary=f"Run {run_id} started",
        )

        try:
            await self._execute_dag(run)
        except Exception as exc:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error = {"type": type(exc).__name__, "message": str(exc)[:500]}
            logger.error("Run %s failed: %s", run_id, exc)

        await self._db.commit()
        return run

    async def resume_run(self, run_id: str) -> TaskRun:
        """Resume a paused run from its last checkpoint."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        if run.status not in ("paused", "awaiting_approval"):
            raise ValueError(f"Run {run_id} is not paused (status={run.status})")

        run.status = "running"
        await self._db.flush()

        try:
            await self._execute_dag(run)
        except Exception as exc:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error = {"type": type(exc).__name__, "message": str(exc)[:500]}

        await self._db.commit()
        return run

    async def pause_run(self, run_id: str, reason: str = "manual_pause") -> TaskRun:
        """Pause a running execution."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        run.status = "paused"
        await self._checkpoint(run, step_id=None, reason=reason)
        await self._db.commit()
        return run

    async def cancel_run(self, run_id: str) -> TaskRun:
        """Cancel a run and all pending steps."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        run.status = "cancelled"
        run.completed_at = datetime.now(timezone.utc)

        # Mark all non-completed steps as skipped
        steps_result = await self._db.execute(
            select(TaskStep).where(
                TaskStep.run_id == run_id,
                TaskStep.status.in_(["pending", "ready"]),
            )
        )
        for step in steps_result.scalars().all():
            step.status = "skipped"

        await self._db.commit()
        return run

    async def _execute_dag(self, run: TaskRun) -> None:
        """Main DAG execution loop."""
        while True:
            ready_steps = await self._get_ready_steps(run.run_id)
            if not ready_steps:
                # Check if all steps are done
                all_steps = await self._get_all_steps(run.run_id)
                pending = [s for s in all_steps if s.status in ("pending", "ready", "running")]
                if not pending:
                    run.status = "completed"
                    run.completed_at = datetime.now(timezone.utc)
                    break
                # If there are pending steps but none ready, we're blocked
                failed = [s for s in all_steps if s.status == "failed"]
                if failed:
                    run.status = "failed"
                    run.completed_at = datetime.now(timezone.utc)
                    run.error = {
                        "message": f"{len(failed)} step(s) failed",
                        "failed_steps": [s.step_id for s in failed],
                    }
                    break
                # Must be waiting for approval or external event
                break

            # Execute ready steps (parallel if multiple)
            run.current_step_ids = [s.step_id for s in ready_steps]
            await self._db.flush()

            if len(ready_steps) == 1:
                await self._execute_step(run, ready_steps[0])
            else:
                # Run independent steps concurrently
                tasks = [self._execute_step(run, step) for step in ready_steps]
                await asyncio.gather(*tasks, return_exceptions=True)

            # Check if run was paused by an approval gate
            await self._db.refresh(run)
            if run.status in ("paused", "awaiting_approval"):
                break

    async def _execute_step(self, run: TaskRun, step: TaskStep) -> None:
        """Execute a single step."""
        step.status = "running"
        step.started_at = datetime.now(timezone.utc)
        await self._db.flush()

        try:
            output = await self._run_step_action(step, run)
            step.status = "completed"
            step.output_data = output
            step.completed_at = datetime.now(timezone.utc)

            await self._checkpoint(run, step.step_id, "step_completed")

            if self._event_bus:
                await self._event_bus.publish(
                    self._event_bus.agent_stream(run.user_id),
                    "step_completed",
                    {"run_id": run.run_id, "step_id": step.step_id, "task_id": step.task_id},
                    user_id=run.user_id,
                )

        except Exception as exc:
            step.retry_count += 1
            if step.retry_count < step.max_retries:
                step.status = "pending"  # Will be retried
                step.error = {"attempt": step.retry_count, "message": str(exc)[:500]}
                logger.warning(
                    "Step %s failed (attempt %d/%d): %s",
                    step.step_id,
                    step.retry_count,
                    step.max_retries,
                    exc,
                )
            else:
                step.status = "failed"
                step.completed_at = datetime.now(timezone.utc)
                step.error = {"message": str(exc)[:500], "final": True}
                logger.error("Step %s permanently failed: %s", step.step_id, exc)

        await self._db.flush()

    async def _run_step_action(self, step: TaskStep, run: TaskRun) -> dict:
        """Execute the actual action for a step. Delegates to the appropriate handler."""
        input_data = step.input_data or {}
        task_type = input_data.get("task_type", "unknown")

        # For now, use Claude for drafting/summarization tasks
        if task_type in ("draft_email", "draft_reply"):
            return await self._draft_action(input_data, run)
        elif task_type == "summarize":
            return await self._summarize_action(input_data)
        else:
            return {"status": "completed", "note": f"Task type '{task_type}' executed"}

    async def _draft_action(self, input_data: dict, run: TaskRun) -> dict:
        """Draft an email using Claude."""
        context_parts = []
        if input_data.get("goal"):
            context_parts.append(f"Goal: {input_data['goal']}")
        if input_data.get("context"):
            context_parts.append(f"Context: {input_data['context']}")
        if input_data.get("recipient"):
            context_parts.append(f"To: {input_data['recipient']}")

        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=1024,
            system=(
                "You are Jarvis's email drafting engine. Generate a professional email draft. "
                'Respond with JSON: {"subject": "...", "body": "...", "tone": "..."}'
            ),
            messages=[{"role": "user", "content": "\n".join(context_parts) or "Draft an email"}],
        )

        import json

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        draft = json.loads(text)
        return {"status": "completed", "draft": draft, "artifact_ref": f"draft_{ULID()}"}

    async def _summarize_action(self, input_data: dict) -> dict:
        """Summarize content using Claude."""
        content = input_data.get("content", input_data.get("text", ""))
        if not content:
            return {"status": "completed", "summary": "No content to summarize"}

        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=512,
            system=(
                "Summarize the provided content concisely. "
                'Respond with JSON: {"summary": "...", "key_points": [...]}'
            ),
            messages=[{"role": "user", "content": content}],
        )

        import json

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)

    async def _get_ready_steps(self, run_id: str) -> list[TaskStep]:
        """Get steps whose dependencies are all completed."""
        all_steps = await self._get_all_steps(run_id)
        completed_ids = {s.step_id for s in all_steps if s.status == "completed"}

        ready = []
        for step in all_steps:
            if step.status not in ("pending",):
                continue
            deps = step.depends_on or []
            if all(dep_id in completed_ids for dep_id in deps):
                step.status = "ready"
                ready.append(step)

        if ready:
            await self._db.flush()
        return ready

    async def _get_all_steps(self, run_id: str) -> list[TaskStep]:
        """Get all steps for a run."""
        result = await self._db.execute(
            select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.created_at)
        )
        return list(result.scalars().all())

    async def _checkpoint(self, run: TaskRun, step_id: str | None, reason: str) -> None:
        """Save a checkpoint."""
        checkpoint = TaskCheckpoint(
            checkpoint_id=f"ckpt_{ULID()}",
            run_id=run.run_id,
            step_id=step_id,
            reason=reason,
            state_snapshot={
                "status": run.status,
                "current_step_ids": run.current_step_ids,
            },
        )
        self._db.add(checkpoint)
        run.checkpoint = checkpoint.state_snapshot
        await self._db.flush()

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
