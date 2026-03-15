"""Operator — orchestrates plan execution.

Thin wrapper that delegates to GraphExecutor for DAG-based execution
with checkpoints, parallel steps, and approval gates. Falls back to
sequential execution for backward compatibility when GraphExecutor
is unavailable.

Responsibilities:
- Bridge between Execution records and the graph executor
- Track execution state machine (task runs, artifacts)
- Report status back for presentation
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.models.executions import Execution, ExecutionTaskRun
from src.models.plans import Plan, PlanTask
from src.services.audit import AuditService
from src.services.retry import retry_async

if TYPE_CHECKING:
    from src.services.graph_executor import GraphExecutor

logger = logging.getLogger(__name__)

DRAFT_EMAIL_PROMPT = """\
You are Jarvis's email drafting engine. Generate a professional email draft \
based on the provided context.

You MUST respond with valid JSON matching this schema:
{
  "subject": "email subject line",
  "body": "full email body text",
  "tone": "the tone used (professional, casual, urgent, etc.)"
}

Rules:
- Match the tone specified in input_data, default to professional
- Keep it concise — founders are busy
- Include a clear call to action when appropriate
- Do not include greetings like "Dear" unless the context suggests formality
"""

SUMMARIZE_PROMPT = """\
You are Jarvis's summarization engine. Produce a concise summary of \
the provided content.

Respond with valid JSON:
{
  "summary": "the summary text",
  "key_points": ["point 1", "point 2", ...]
}
"""


class Operator:
    """Execute approved plans — delegates to GraphExecutor when available."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        notifier=None,
        graph_executor: GraphExecutor | None = None,
    ):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._audit = AuditService(db)
        self._notifier = notifier
        self._graph_executor = graph_executor

    async def execute_plan(self, execution_id: str, user_id: str) -> bool:
        """Execute all tasks in a plan. Returns True on success."""
        result = await self._db.execute(
            select(Execution).where(Execution.execution_id == execution_id)
        )
        execution = result.scalar_one_or_none()
        if not execution:
            logger.error("Execution not found: %s", execution_id)
            return False

        result = await self._db.execute(select(Plan).where(Plan.plan_id == execution.plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            logger.error("Plan not found: %s", execution.plan_id)
            return False

        # Delegate to GraphExecutor if available
        if self._graph_executor:
            return await self._execute_via_graph(execution, plan, user_id)

        # Fallback: sequential execution
        return await self._execute_sequential(execution, plan, user_id)

    async def _execute_via_graph(self, execution: Execution, plan: Plan, user_id: str) -> bool:
        """Execute using the DAG-based graph executor."""
        try:
            run = await self._graph_executor.create_run(plan.plan_id, user_id)

            execution.status = "running"
            await self._db.flush()

            await self._audit.log(
                user_id=user_id,
                action_type="execution_started",
                plan_id=plan.plan_id,
                execution_id=execution.execution_id,
                summary=f"Executing plan via graph: {plan.goal}",
            )

            completed_run = await self._graph_executor.execute_run(run.run_id)
            success = completed_run.status == "completed"

            execution.status = "completed" if success else "failed"
            execution.current_task_id = None
            plan.status = "completed" if success else "failed"

            await self._audit.log(
                user_id=user_id,
                action_type="execution_completed" if success else "execution_failed",
                plan_id=plan.plan_id,
                execution_id=execution.execution_id,
                summary=f"Plan '{plan.goal}' {'completed' if success else 'failed'}",
            )

            await self._db.commit()
            await self._notify_completion(execution, plan, user_id, success)

            logger.info(
                "Graph execution %s %s for plan %s",
                execution.execution_id,
                "completed" if success else "failed",
                plan.plan_id,
            )
            return success

        except Exception as exc:
            logger.error("Graph execution failed, falling back to sequential: %s", exc)
            return await self._execute_sequential(execution, plan, user_id)

    async def _execute_sequential(self, execution: Execution, plan: Plan, user_id: str) -> bool:
        """Sequential execution fallback (original logic)."""
        result = await self._db.execute(
            select(PlanTask).where(PlanTask.plan_id == plan.plan_id).order_by(PlanTask.id)
        )
        tasks = list(result.scalars().all())

        execution.status = "running"
        await self._db.flush()

        await self._audit.log(
            user_id=user_id,
            action_type="execution_started",
            plan_id=plan.plan_id,
            execution_id=execution.execution_id,
            summary=f"Executing plan: {plan.goal}",
        )

        success = True
        for task in tasks:
            execution.current_task_id = task.task_id

            task_run = ExecutionTaskRun(
                execution_id=execution.execution_id,
                task_id=task.task_id,
                status="running",
            )
            self._db.add(task_run)
            await self._db.flush()

            try:
                result_data = await self._execute_task(task, plan)
                task_run.status = "completed"
                task_run.result_data = result_data
                task.status = "completed"

                if result_data and result_data.get("artifact_ref"):
                    task_run.artifact_ref = result_data["artifact_ref"]

            except Exception as exc:
                task_run.status = "failed"
                task_run.error_message = str(exc)
                task.status = "failed"
                success = False
                logger.error("Task %s failed: %s", task.task_id, exc)
                break

            await self._db.flush()

        execution.status = "completed" if success else "failed"
        execution.current_task_id = None
        plan.status = "completed" if success else "failed"

        await self._audit.log(
            user_id=user_id,
            action_type="execution_completed" if success else "execution_failed",
            plan_id=plan.plan_id,
            execution_id=execution.execution_id,
            summary=f"Plan '{plan.goal}' {'completed' if success else 'failed'}",
        )

        await self._db.commit()
        await self._notify_completion(execution, plan, user_id, success)

        logger.info(
            "Execution %s %s (%d tasks)",
            execution.execution_id,
            "completed" if success else "failed",
            len(tasks),
        )
        return success

    async def _notify_completion(
        self, execution: Execution, plan: Plan, user_id: str, success: bool
    ) -> None:
        """Notify user about execution completion."""
        if self._notifier:
            try:
                status = "completed" if success else "failed"
                await self._notifier.notify(
                    user_id=user_id,
                    notification_type="info_update",
                    title=f"Task {status}: {plan.goal}",
                    body=f"Execution {execution.execution_id} {status}.",
                    data={"plan_id": plan.plan_id, "execution_id": execution.execution_id},
                )
            except Exception:
                logger.warning("Failed to notify for execution", exc_info=True)

    async def _execute_task(self, task: PlanTask, plan: Plan) -> dict:
        """Execute a single task and return result data."""
        task_type = task.task_type
        input_data = task.input_data or {}

        if task_type in ("draft_email", "draft_reply", "draft_email_reply"):
            return await self._draft_email(input_data, plan)
        elif task_type == "summarize":
            return await self._summarize(input_data)
        elif task_type in ("fetch_info", "search_memory"):
            return {"status": "completed", "note": "Info fetch stub"}
        elif task_type in ("add_to_brief", "acknowledge"):
            return {"status": "completed"}
        else:
            return {"status": "completed", "note": f"Task type '{task_type}' executed (stub)"}

    @retry_async(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=(anthropic.APIConnectionError, anthropic.RateLimitError),
    )
    async def _draft_email(self, input_data: dict, plan: Plan) -> dict:
        """Generate an email draft using Claude."""
        context_parts = [f"Goal: {plan.goal}"]
        if input_data.get("tone"):
            context_parts.append(f"Tone: {input_data['tone']}")
        if input_data.get("recipient"):
            context_parts.append(f"To: {input_data['recipient']}")
        if input_data.get("context"):
            context_parts.append(f"Context: {input_data['context']}")
        if plan.reasoning_summary:
            context_parts.append(f"Background: {plan.reasoning_summary}")

        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=1024,
            system=DRAFT_EMAIL_PROMPT,
            messages=[{"role": "user", "content": "\n".join(context_parts)}],
        )

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        draft = json.loads(text)

        artifact_ref = f"draft_{ULID()}"
        return {
            "artifact_ref": artifact_ref,
            "draft": draft,
            "status": "completed",
        }

    @retry_async(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=(anthropic.APIConnectionError, anthropic.RateLimitError),
    )
    async def _summarize(self, input_data: dict) -> dict:
        """Generate a summary using Claude."""
        content = input_data.get("content", input_data.get("text", ""))
        if not content:
            return {"status": "completed", "summary": "No content to summarize"}

        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=512,
            system=SUMMARIZE_PROMPT,
            messages=[{"role": "user", "content": content}],
        )

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
