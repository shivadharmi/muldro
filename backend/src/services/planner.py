"""Planner — decides what should happen next.

The Planner is the brain of Jarvis. It reads events, world model state,
and memory to produce structured task graphs.

Responsibilities:
- Interpret context from events and user commands
- Decide: ignore / add_to_brief / summarize_now / create_task / draft_reply / etc.
- Produce structured Plan with PlanTasks
- Set execution mode (approval_required, draft_only, auto_execute)

The Planner uses Claude for reasoning but MUST output structured JSON,
not free-form text. The plan contract is the most important schema.
"""

import json
import logging

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings
from src.models.plans import Plan, PlanTask

logger = logging.getLogger(__name__)

DECISIONS = [
    "acknowledge",
    "answer_directly",
    "create_task",
    "draft_reply",
    "search_memory",
    "add_to_brief",
    "ignore",
]

PLAN_SYSTEM_PROMPT = """\
You are Jarvis's planning engine. Given a user command and optional context, \
produce a structured plan.

You MUST respond with valid JSON matching this schema:
{
  "decision": one of %s,
  "goal": "1-sentence description of what the plan achieves",
  "reasoning_summary": "brief explanation of why this decision",
  "priority": "low" | "medium" | "high" | "critical",
  "risk_level": "none" | "low" | "medium" | "high",
  "execution_mode": "auto_execute" | "approval_required" | "draft_only",
  "tasks": [
    {
      "task_type": "string — what kind of task (e.g. fetch_info, draft_email, summarize)",
      "input_data": { ... any relevant parameters ... }
    }
  ]
}

Rules:
- "acknowledge" = simple confirmation, no tasks needed
- "answer_directly" = you can answer from the command alone, provide answer in goal
- "create_task" = needs execution (fetch data, draft email, etc.), always include tasks
- "draft_reply" = compose an email/message draft, needs approval
- "search_memory" = need to look up past context before acting
- If the command is a question you can answer, use "answer_directly"
- External writes (sending emails, creating events) MUST use "approval_required"
- Read-only operations can use "auto_execute"
- Keep tasks minimal — 1-3 tasks for most commands
""" % json.dumps(DECISIONS)


class Planner:
    """Generate structured plans from events or user commands."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def plan_for_command(
        self, command: str, user_id: str, context: str | None = None
    ) -> Plan:
        """Create a plan from a direct user command. Returns the Plan object."""
        user_message = f"Command: {command}"
        if context:
            user_message += f"\nContext: {context}"

        raw_plan = await self._call_claude(user_message)
        plan = await self._store_plan(raw_plan, user_id, trigger_type="command", trigger_ref=None)
        return plan

    async def plan_for_event(self, event_id: str, user_id: str) -> Plan | None:
        """Create a plan in response to a normalized event. Returns Plan or None."""
        # TODO: Fetch event details from DB, enrich with entity/memory context
        # For now, this is a placeholder for Sprint 2
        return None

    async def _call_claude(self, user_message: str) -> dict:
        """Call Claude with structured output and parse the JSON response."""
        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=1024,
            system=PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)

    async def _store_plan(
        self,
        raw: dict,
        user_id: str,
        trigger_type: str,
        trigger_ref: str | None,
    ) -> Plan:
        """Persist a plan and its tasks to the database."""
        plan_id = f"plan_{ULID()}"

        plan = Plan(
            plan_id=plan_id,
            user_id=user_id,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            goal=raw.get("goal", ""),
            priority=raw.get("priority", "medium"),
            decision=raw.get("decision", "acknowledge"),
            reasoning_summary=raw.get("reasoning_summary"),
            risk_level=raw.get("risk_level", "low"),
            execution_mode=raw.get("execution_mode", "approval_required"),
            status="created",
        )

        tasks = []
        for i, task_data in enumerate(raw.get("tasks", [])):
            task = PlanTask(
                task_id=f"ptask_{ULID()}",
                plan_id=plan_id,
                task_type=task_data.get("task_type", "unknown"),
                input_data=task_data.get("input_data"),
                status="pending",
            )
            tasks.append(task)

        plan.tasks = tasks
        self._db.add(plan)
        await self._db.commit()
        await self._db.refresh(plan)

        logger.info(
            "Plan created: %s decision=%s tasks=%d",
            plan_id,
            plan.decision,
            len(tasks),
        )
        return plan
