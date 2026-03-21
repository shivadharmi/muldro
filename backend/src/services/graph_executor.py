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
from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskCheckpoint, TaskRun, TaskStep
from src.orchestrator.contracts import StepResult, ToolCallRequest, ToolCallResult
from src.services.audit import AuditService
from src.services.execution_state import transition_run, transition_step

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

    async def create_run(self, plan_id: str, user_id: str, workspace_id: str = "") -> TaskRun:
        """Create a TaskRun from a Plan, building the step DAG."""
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
            source="plan",
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
                    query=plan.goal[:500] if plan.goal else "",
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

            step = TaskStep(
                step_id=step_id,
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                task_id=task.task_id,
                plan_task_id=task.task_id,
                depends_on=depends_on_step_ids or None,
                status="pending",
                input_data=task.input_data,
            )
            self._db.add(step)

        await self._db.flush()
        logger.info(
            "Run %s populated with %d steps for plan %s",
            run.run_id,
            len(tasks),
            plan.plan_id,
        )

    async def execute_run(self, run_id: str, trace_id: str | None = None) -> TaskRun:
        """Execute a run's DAG to completion (or pause at approval gate)."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        if trace_id:
            run.trace_id = trace_id

        transition_run(run, "running")
        run.started_at = datetime.now(timezone.utc)
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

        try:
            await self._execute_dag(run)
        except Exception as exc:
            transition_run(run, "failed")
            run.completed_at = datetime.now(timezone.utc)
            run.error = {"type": type(exc).__name__, "message": str(exc)[:500]}
            logger.error("Run %s failed: %s", run_id, exc)
            await self._emit_event(
                "run.failed",
                run.user_id,
                {
                    "run_id": run_id,
                    "error": str(exc)[:500],
                },
                workspace_id=run.workspace_id,
            )

        # Record Prometheus metrics
        try:
            from src.services.metrics_service import MetricsService

            MetricsService.record_execution_completed(run.status)
        except Exception:
            pass

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

        transition_run(run, "running")
        await self._db.flush()

        try:
            await self._execute_dag(run)
        except Exception as exc:
            transition_run(run, "failed")
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

        transition_run(run, "paused")
        await self._checkpoint(run, step_id=None, reason=reason)
        await self._db.commit()
        return run

    async def cancel_run(self, run_id: str) -> TaskRun:
        """Cancel a run and all pending steps."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        transition_run(run, "cancelled")
        run.completed_at = datetime.now(timezone.utc)

        # Mark all non-completed steps as skipped
        steps_result = await self._db.execute(
            select(TaskStep).where(
                TaskStep.run_id == run_id,
                TaskStep.status.in_(["pending", "ready"]),
            )
        )
        for step in steps_result.scalars().all():
            transition_step(step, "skipped")
            await self._emit_event(
                "step.skipped",
                run.user_id,
                {"run_id": run_id, "step_id": step.step_id},
                workspace_id=run.workspace_id,
            )

        await self._db.commit()
        await self._emit_event(
            "run.cancelled", run.user_id, {"run_id": run_id},
            workspace_id=run.workspace_id,
        )
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
                    transition_run(run, "completed")
                    run.completed_at = datetime.now(timezone.utc)
                    # Run verifier if available
                    if self._verifier:
                        await self._run_verification(run)
                    # Writeback memories from execution results
                    await self._writeback_memories(run)
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
        """Execute a single step, with approval gate if required."""
        # Check if step requires approval via ToolRegistry
        if self._tool_registry and step.input_data:
            task_type = step.input_data.get("task_type", "")
            tool = await self._tool_registry.get_tool(task_type)
            if tool and tool.requires_approval:
                # Create approval record and pause
                from src.services.approval_service import create_approval

                approval = await create_approval(
                    self._db,
                    user_id=run.user_id,
                    workspace_id=run.workspace_id,
                    approval_type=f"step:{task_type}",
                    title=f"Approve step: {step.name or task_type}",
                    summary=f"Step in run {run.run_id} requires approval",
                    risk_level=tool.risk_level,
                    execution_id=run.run_id,
                    run_id=run.run_id,
                    step_id=step.step_id,
                    requested_by=run.user_id,
                )
                transition_step(step, "waiting_approval")
                transition_run(run, "awaiting_approval")
                await self._checkpoint(run, step.step_id, "approval_gate")
                await self._db.flush()

                if self._notifier:
                    try:
                        await self._notifier.notify(
                            user_id=run.user_id,
                            notification_type="approval_request",
                            title=f"Approve: {step.name or task_type}",
                            body=f"Step requires approval in run {run.run_id}",
                            data={
                                "approval_id": approval.approval_id,
                                "run_id": run.run_id,
                                "step_id": step.step_id,
                            },
                            workspace_id=run.workspace_id,
                        )
                    except Exception:
                        logger.warning("Failed to notify for step approval", exc_info=True)
                return

        transition_step(step, "running")
        step.started_at = datetime.now(timezone.utc)
        await self._db.flush()
        await self._emit_event(
            "step.started",
            run.user_id,
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
            },
            workspace_id=run.workspace_id,
        )

        t0 = time.monotonic()
        try:
            output = await self._run_step_action(step, run)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            transition_step(step, "completed")
            step.output_data = output
            step.completed_at = datetime.now(timezone.utc)

            result = StepResult(
                step_id=step.step_id,
                status="completed",
                output_data=output,
                duration_ms=elapsed_ms,
            )

            await self._checkpoint(run, step.step_id, "step_completed")

            if self._event_bus:
                await self._event_bus.publish(
                    self._event_bus.agent_stream(run.user_id),
                    "step_completed",
                    {
                        "run_id": run.run_id,
                        "step_id": step.step_id,
                        "task_id": step.task_id,
                        "duration_ms": result.duration_ms,
                    },
                    user_id=run.user_id,
                )
                # Emit surface.updated for A2UI live streaming
                if output and any(
                    k in output for k in ("draft", "report", "summary", "result", "view")
                ):
                    await self._emit_event(
                        "surface.updated",
                        run.user_id,
                        {
                            "run_id": run.run_id,
                            "step_id": step.step_id,
                            "surface_type": "step_output",
                            "preview": str(output.get("result", output.get("summary", "")))[:200],
                        },
                        workspace_id=run.workspace_id,
                    )

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            step.retry_count += 1
            if step.retry_count < step.max_retries:
                transition_step(step, "failed")
                transition_step(step, "pending")  # Retry: failed → pending
                step.error = {"attempt": step.retry_count, "message": str(exc)[:500]}
                logger.warning(
                    "Step %s failed (attempt %d/%d): %s",
                    step.step_id,
                    step.retry_count,
                    step.max_retries,
                    exc,
                )
            else:
                transition_step(step, "failed")
                step.completed_at = datetime.now(timezone.utc)
                step.error = {"message": str(exc)[:500], "final": True}
                logger.error("Step %s permanently failed: %s", step.step_id, exc)
                StepResult(
                    step_id=step.step_id,
                    status="failed",
                    error=str(exc)[:500],
                    duration_ms=elapsed_ms,
                )
                await self._emit_event(
                    "step.failed",
                    run.user_id,
                    {
                        "run_id": run.run_id,
                        "step_id": step.step_id,
                        "error": str(exc)[:500],
                        "duration_ms": elapsed_ms,
                    },
                    workspace_id=run.workspace_id,
                )

        await self._db.flush()

    async def _run_step_action(self, step: TaskStep, run: TaskRun) -> dict:
        """Execute the actual action for a step.

        Resolution order:
        1. MCP bridge (external MCP servers)
        2. ToolRegistry → connector dispatch
        3. Built-in Claude handlers (draft_email, summarize)
        4. Generic Claude handler (any task_type with goal/context)
        5. Stub completion for truly unknown types
        """
        input_data = step.input_data or {}
        task_type = input_data.get("task_type", "unknown")

        request = ToolCallRequest(
            tool_name=task_type,
            parameters=input_data,
        )

        # Enrich input with context if ContextBuilder is available
        context_prompt = await self._build_step_context(run, step)

        t0 = time.monotonic()

        # 1. Try capability resolver (routes to best backend)
        try:
            from src.integrations.capabilities import get_capability_for_tool
            from src.integrations.capability_resolver import CapabilityResolver

            capability = get_capability_for_tool(task_type)
            if capability:
                resolver = CapabilityResolver(self._db, None, run.workspace_id)
                raw = await resolver.execute(task_type, input_data, user_id=run.user_id)
                ToolCallResult(
                    tool_name=request.tool_name,
                    status="success",
                    result=raw,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
                return raw
        except Exception:
            logger.debug("Capability resolver failed for %s, falling back", task_type)

        # 2. Try MCP bridge (external MCP servers)
        from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

        if is_mcp_tool(task_type):
            raw = await call_mcp_tool(task_type, input_data)
            ToolCallResult(
                tool_name=request.tool_name,
                status="success",
                result=raw,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            return raw

        # 3. Try connector dispatch via ToolRegistry
        if self._tool_registry:
            tool_def = await self._tool_registry.get_tool(task_type)
            if tool_def:
                request.requires_approval = tool_def.requires_approval
                if tool_def.connector_type and tool_def.connector_type != "internal":
                    raw = await self._execute_via_connector(
                        tool_def, task_type, input_data, context_prompt
                    )
                    ToolCallResult(
                        tool_name=request.tool_name,
                        status="success",
                        result=raw,
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                    return raw

        # 4. Built-in Claude handlers for specific types
        if task_type in ("draft_email", "draft_reply"):
            return await self._draft_action(input_data, run, context_prompt)
        if task_type == "summarize":
            return await self._summarize_action(input_data, context_prompt)

        # 5. Generic Claude handler — any task with a goal/context gets
        #    routed to Claude for intelligent handling
        goal = input_data.get("goal", input_data.get("context", ""))
        if goal:
            return await self._generic_claude_action(task_type, input_data, context_prompt)

        # 6. Stub — log so we know what's unhandled
        logger.info("Step %s: no handler for task_type '%s', stub", step.step_id, task_type)
        return {"status": "completed", "note": f"Task type '{task_type}' executed"}

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
                query=query[:500] if query else "",
                task_type=task_type,
            )
            from src.services.context_builder import ContextBuilder

            return ContextBuilder.to_prompt(pack)
        except Exception:
            logger.debug("ContextBuilder failed for step %s", step.step_id, exc_info=True)
            return ""

    async def _execute_via_connector(
        self,
        tool_def,
        task_type: str,
        input_data: dict,
        context_prompt: str,
    ) -> dict:
        """Execute a step by dispatching to the appropriate connector."""
        from src.connectors.base import CONNECTOR_REGISTRY

        connector_type = tool_def.connector_type
        connector_cls = CONNECTOR_REGISTRY.get(connector_type)
        if not connector_cls:
            return {
                "status": "error",
                "error": f"No connector for type: {connector_type}",
            }

        credentials = {}
        if self._connector_credentials_fn:
            try:
                credentials = await self._connector_credentials_fn(connector_type)
            except Exception:
                logger.warning("No credentials for connector %s", connector_type)

        connector = connector_cls(self._settings)
        # Derive action from task_type (e.g. gmail_send → send)
        action = task_type
        if task_type.startswith(f"{connector_type}_"):
            action = task_type[len(connector_type) + 1 :]

        result = await connector.execute_action(action, input_data, credentials)
        return {**result, "dispatched_via": "connector", "connector_type": connector_type}

    async def _draft_action(self, input_data: dict, run: TaskRun, context_prompt: str = "") -> dict:
        """Draft an email using Claude."""
        context_parts = []
        if input_data.get("goal"):
            context_parts.append(f"Goal: {input_data['goal']}")
        if input_data.get("context"):
            context_parts.append(f"Context: {input_data['context']}")
        if input_data.get("recipient"):
            context_parts.append(f"To: {input_data['recipient']}")
        if context_prompt:
            context_parts.append(f"\n--- Background ---\n{context_prompt}")

        response = await self._client.messages.create(
            model=self._settings.resolved_model,
            max_tokens=1024,
            system=(
                "You are Jarvis's email drafting engine. Generate a professional email draft. "
                'Respond with JSON: {"subject": "...", "body": "...", "tone": "..."}'
            ),
            messages=[{"role": "user", "content": "\n".join(context_parts) or "Draft an email"}],
        )

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        draft = json.loads(text)
        return {"status": "completed", "draft": draft, "artifact_ref": f"draft_{ULID()}"}

    async def _summarize_action(self, input_data: dict, context_prompt: str = "") -> dict:
        """Summarize content using Claude."""
        content = input_data.get("content", input_data.get("text", ""))
        if not content:
            return {"status": "completed", "summary": "No content to summarize"}

        system = "Summarize the provided content concisely. "
        system += 'Respond with JSON: {"summary": "...", "key_points": [...]}'
        if context_prompt:
            system += f"\n\n--- Background ---\n{context_prompt}"

        response = await self._client.messages.create(
            model=self._settings.resolved_model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": content}],
        )

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)

    async def _generic_claude_action(
        self, task_type: str, input_data: dict, context_prompt: str = ""
    ) -> dict:
        """Handle any task type by routing to Claude with structured instructions."""
        goal = input_data.get("goal", input_data.get("context", ""))
        parts = [f"Task type: {task_type}", f"Goal: {goal}"]
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

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": "completed", "result": text}

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
                transition_step(step, "ready")
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
            workspace_id=run.workspace_id,
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
                parts.append(f"- {step.task_id}: {json.dumps(step.output_data)[:200]}")
            await self._memory_service.extract_and_store(
                user_id=run.user_id,
                source_text="\n".join(parts),
                source_event_ids=[run.run_id],
            )
        except Exception:
            logger.debug("Memory writeback failed", exc_info=True)

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
        except Exception:
            logger.warning("Verification failed for run %s", run.run_id, exc_info=True)

    async def _emit_event(
        self,
        event_type: str,
        user_id: str,
        payload: dict,
        workspace_id: str | None = None,
    ) -> None:
        """Publish a domain event (best-effort) + Redis progress + DB persistence."""
        if self._event_bus:
            try:
                stream = self._event_bus.agent_stream(user_id)
                await self._event_bus.publish(stream, event_type, payload, user_id)
            except Exception:
                logger.debug("Failed to emit %s event", event_type, exc_info=True)

        # Persist to runtime_events table for home feed / runtime activity
        run_id = payload.get("run_id")
        step_id = payload.get("step_id")
        if workspace_id:
            try:
                from src.models.runtime_event import RuntimeEvent

                self._db.add(
                    RuntimeEvent(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        step_id=step_id,
                        event_type=event_type.replace(".", "_"),
                        payload=payload,
                    )
                )
                await self._db.flush()
            except Exception:
                logger.debug("Failed to persist runtime event %s", event_type, exc_info=True)

        # Publish to Redis for WebSocket progress streaming
        if run_id:
            await self._publish_progress(run_id, {"event_type": event_type, **payload})

    async def _publish_progress(self, run_id: str, data: dict) -> None:
        """Publish step progress to Redis pubsub for WebSocket consumers."""
        try:
            import redis.asyncio as aioredis

            redis = aioredis.from_url(self._settings.redis_url)
            try:
                channel = f"jarvis:run_progress:{run_id}"
                await redis.publish(channel, json.dumps(data))
            finally:
                await redis.close()
        except Exception:
            logger.debug("Failed to publish run progress", exc_info=True)

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
