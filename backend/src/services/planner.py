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

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings
from src.models.events import NormalizedEvent
from src.models.plans import Plan, PlanTask

if TYPE_CHECKING:
    from src.services.memory_service import MemoryService
    from src.services.world_model import WorldModel

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
You are Jarvis's planning engine. Given a user command or event with context, \
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
- "add_to_brief" = informational event, include in next daily briefing
- "ignore" = low-value event, no action needed
- For incoming emails: prefer "add_to_brief" unless they contain a direct question or action
- External writes (sending emails, creating events) MUST use "approval_required"
- Read-only operations can use "auto_execute"
- Keep tasks minimal — 1-3 tasks for most commands
""" % json.dumps(DECISIONS)


class Planner:
    """Generate structured plans from events or user commands."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        world_model: WorldModel | None = None,
        memory_service: MemoryService | None = None,
    ):
        self._settings = settings
        self._db = db
        self._world_model = world_model
        self._memory_service = memory_service
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def plan_for_command(
        self, command: str, user_id: str, context: str | None = None
    ) -> Plan:
        """Create a plan from a direct user command. Returns the Plan object."""
        sections = [f"## Command\n{command}"]
        if context:
            sections.append(f"## Additional Context\n{context}")

        enrichment = await self._gather_context(command, user_id)
        if enrichment:
            sections.append(enrichment)

        raw_plan = await self._call_claude("\n\n".join(sections))
        return await self._store_plan(raw_plan, user_id, trigger_type="command", trigger_ref=None)

    async def plan_for_event(self, event_id: str, user_id: str) -> Plan | None:
        """Create a plan in response to a normalized event. Returns Plan or None."""
        result = await self._db.execute(
            select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            logger.warning("Event not found for planning: %s", event_id)
            return None

        if (event.importance_score or 0) < 0.4:
            logger.debug(
                "Event %s below planning threshold (%.2f)",
                event_id,
                event.importance_score or 0,
            )
            return None

        sections = [
            f"## Event\n"
            f"Type: {event.event_type}\n"
            f"Source: {event.source}\n"
            f"Title: {event.title or 'N/A'}\n"
            f"Summary: {event.summary or 'N/A'}\n"
            f"Importance: {event.importance_score:.2f}\n"
            f"Urgency: {event.urgency_score:.2f if event.urgency_score else 'N/A'}"
        ]

        if event.actor_entities:
            sections.append(f"## Actors\n{json.dumps(event.actor_entities, indent=2)}")

        enrichment = await self._gather_context(event.title or event.summary or "", user_id)
        if enrichment:
            sections.append(enrichment)

        raw_plan = await self._call_claude("\n\n".join(sections))
        return await self._store_plan(raw_plan, user_id, trigger_type="event", trigger_ref=event_id)

    async def _gather_context(self, query: str, user_id: str) -> str | None:
        """Gather entity and memory context for enriched planning."""
        parts = []

        if self._world_model:
            entities = await self._world_model.find_entity(user_id, query)
            if entities:
                entity_lines = [
                    f"- {e['canonical_name']} ({e['entity_type']})" for e in entities[:5]
                ]
                parts.append("## Related Entities\n" + "\n".join(entity_lines))

        if self._memory_service:
            memories = await self._memory_service.retrieve(user_id, query, max_results=5)
            if memories:
                mem_lines = [f"- {m.get('fact_text', '')}" for m in memories[:5]]
                parts.append("## Relevant Memories\n" + "\n".join(mem_lines))

        return "\n\n".join(parts) if parts else None

    async def _call_claude(self, user_message: str) -> dict:
        """Call Claude with structured output and parse the JSON response."""
        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=1024,
            system=PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text
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
        for task_data in raw.get("tasks", []):
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
