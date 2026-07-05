"""StepGraphStore — step-DAG persistence for the graph executor.

Extracted from ``GraphExecutor`` (god-object decomposition, 2026-06-20). Owns the
read/write of the step graph on the shared ``AsyncSession``: building the step DAG
from a plan, querying all/ready steps, resolving inter-step output references, and
writing rich checkpoints. It is a leaf under ``src.services`` — it depends only on
the session and (for context-pack pre-building) a ``ContextBuilder``; it never
imports ``graph_executor``.

Status changes go through ``transition_step`` (never direct mutation), preserving
the execution state machine.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from ulid import ULID

from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskCheckpoint, TaskRun, TaskStep
from src.services.execution_state import TERMINAL_SUCCESS, transition_step

logger = logging.getLogger(__name__)


class StepGraphStore:
    """Persists and queries the step DAG for a run on the shared session."""

    def __init__(self, db, context_builder=None):
        self._db = db
        self._context_builder = context_builder

    @staticmethod
    def build_graph_definition(tasks: list[PlanTask]) -> dict:
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

    async def populate_steps(self, run: TaskRun, plan: Plan) -> None:
        """Build step DAG from plan tasks onto an existing run."""
        result = await self._db.execute(
            select(PlanTask).where(PlanTask.plan_id == plan.plan_id).order_by(PlanTask.id)
        )
        tasks = list(result.scalars().all())

        graph_def = self.build_graph_definition(tasks)
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

    async def get_ready_steps(self, run_id: str) -> list[TaskStep]:
        """Get steps whose dependencies are all completed.

        Also picks up steps already in 'ready' state (e.g. from a previous
        iteration where execution failed before the step could start) and
        steps in 'running' state from approval resumption (the approval
        handler transitions waiting_approval → running before the scheduler
        resumes the DAG).
        """
        all_steps = await self.get_all_steps(run_id)
        completed_ids = {s.step_id for s in all_steps if s.status in TERMINAL_SUCCESS}

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

    async def get_all_steps(self, run_id: str) -> list[TaskStep]:
        """Get all steps for a run."""
        result = await self._db.execute(
            select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.created_at)
        )
        return list(result.scalars().all())

    async def resolve_step_references(self, step: TaskStep, run_id: str) -> dict:
        """Resolve {task_id}.output.field references in step input_data.

        Enables declarative wiring between DAG steps: a downstream step
        can reference an upstream step's output by task_id.
        """
        input_data = dict(step.input_data or {})
        all_steps = await self.get_all_steps(run_id)
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

    async def checkpoint(self, run: TaskRun, step_id: str | None, reason: str) -> None:
        """Save a rich checkpoint with completed step outputs."""
        # Collect completed step outputs for checkpoint context
        completed_outputs = {}
        try:
            all_steps = await self.get_all_steps(run.run_id)
            completed_outputs = {
                s.step_id: {
                    "task_id": s.task_id,
                    "status": s.status,
                    "output_summary": str(s.output_data) if s.output_data else None,
                }
                for s in all_steps
                if s.status in TERMINAL_SUCCESS
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
        # run.checkpoint shares its JSONB column with application-state keys
        # written by other paths (the ``auto_executed`` trust audit trail, the
        # ``verification`` verdict). checkpoint() owns only the execution-snapshot
        # keys built above — merge so those other keys survive instead of being
        # clobbered. The persisted TaskCheckpoint row still stores pure snapshot.
        run.checkpoint = {**(run.checkpoint or {}), **snapshot}
        await self._db.flush()
