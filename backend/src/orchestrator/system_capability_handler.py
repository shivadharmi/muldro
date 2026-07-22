"""SystemCapabilityHandler — executes ``system.*`` capability steps.

Extracted from :class:`~src.orchestrator.jarvis.JarvisOrchestrator` (ORCH-P3-3)
so the frozen god object no longer carries this business logic. It handles the
four writable system capabilities (``set_goal``, ``set_instruction``,
``schedule_reminder``, ``add_to_brief``), the no-op ``respond``/``acknowledge``
capabilities, and audits each execution as a ``PlanTask`` + ``InteractionLog``.

The orchestrator injects this collaborator via its constructor and delegates to
``handle_system_capability``; per engineering-standards §1, new/relocated
behavior lives on injected collaborators, never as methods on the hub.
"""

import logging

from pydantic import ValidationError
from ulid import ULID

from src.contracts import PlanOutput, PlanStep
from src.errors import classify, new_correlation_id
from src.middleware.observability import get_correlation_id
from src.orchestrator.services import ServiceContainer
from src.tools.schemas import ScheduleReminderInput, SetInstructionStepInput

logger = logging.getLogger(__name__)


def _coerce_instruction_input(raw: object) -> dict:
    """Normalize LLM shape variance for set_instruction into the flat model shape.

    Accepts: the canonical flat dict (``step.input`` itself already shaped like
    ``SetInstructionStepInput``); a nested ``{"instruction": {...}}`` wrapper; a
    nested ``{"instruction": "text"}`` or bare top-level string; anything else
    normalizes to ``{}`` (handled downstream as "no instruction spec provided").
    Returns a dict suitable for ``SetInstructionStepInput.model_validate``.
    """
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("instruction")
    if inner is None:
        inner = raw
    if isinstance(inner, str):
        return {"instruction_text": inner}
    if isinstance(inner, dict):
        # lift nested keys; tolerate the flat shape (inner is raw itself)
        return {
            "instruction_text": inner.get("instruction_text") or raw.get("instruction_text", ""),
            "instruction_type": inner.get("instruction_type")
            or raw.get("instruction_type", "preference"),
            "trigger_conditions": inner.get("trigger_conditions"),
            "schedule_config": inner.get("schedule_config"),
        }
    return {}


def _coerce_schedule_reminder_input(raw: object) -> dict:
    """Normalize LLM shape variance for schedule_reminder into the flat model shape.

    Accepts: the canonical flat ``{"title": ..., "cron_expr": ...}`` dict; the
    legacy ``{"tasks": [{"input_data": {"cron_expr": ...}}]}`` wrapper (tolerant
    of a malformed/non-list ``tasks``); or a bare string (treated as the title).
    Returns a dict suitable for ``ScheduleReminderInput.model_validate``.
    """
    if isinstance(raw, str):
        return {"title": raw}
    if not isinstance(raw, dict):
        return {}
    if "tasks" in raw:
        tasks = raw.get("tasks")
        cron_expr = ""
        if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
            input_data = tasks[0].get("input_data") or {}
            if isinstance(input_data, dict):
                cron_expr = input_data.get("cron_expr") or ""
        return {"title": raw.get("title", ""), "cron_expr": cron_expr}
    return {"title": raw.get("title", ""), "cron_expr": raw.get("cron_expr", "")}


class SystemCapabilityHandler:
    """Route and execute ``system.*`` capability steps against the data layer."""

    def __init__(self, db_factory, services: ServiceContainer, settings=None):
        self._db_factory = db_factory
        self._services = services
        self._settings = settings

    def _request_services(self, db) -> ServiceContainer:
        """DB-bound services for ``db``; reuse an injected container when present.

        Mirrors ``JarvisOrchestrator._request_services`` — in the API path the
        container holds only session-free singletons, so DB-bound services are
        built per request rather than sharing one ``AsyncSession`` (P2 #4).
        """
        from src.runtime import request_services

        return request_services(self._services, self._settings, db)

    async def _handle_set_goal(
        self,
        goal_text: str,
        reasoning: str,
        priority: str,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Store a goal as a memory via MemoryService."""
        title = goal_text or reasoning or "Untitled goal"
        async with self._db_factory() as db:
            memory_svc = self._request_services(db).memory_service
            if not memory_svc:
                return {"status": "error", "error": "Memory service unavailable"}
            memory_id = await memory_svc.store_goal_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                title=title,
                priority=priority,
            )
            await db.commit()
        logger.info("Goal stored as memory %s: %s", memory_id, title)
        return {"status": "created", "memory_id": memory_id, "title": title}

    async def _handle_set_instruction(
        self,
        instruction_text: str,
        reasoning: str,
        instruction: SetInstructionStepInput,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Handle set_instruction: create trigger/schedule/preference memory."""
        inst_text = instruction.instruction_text or instruction_text
        if not inst_text:
            return {"status": "error", "error": "No instruction spec provided"}
        inst_type = instruction.instruction_type or "preference"

        # Store as a preference memory via public API
        async with self._db_factory() as db:
            memory_svc = self._request_services(db).memory_service
            if not memory_svc:
                return {"status": "error", "error": "Memory service unavailable"}
            memory_id = await memory_svc.store_instruction_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                instruction_text=inst_text,
                instruction_type=inst_type,
            )
            await db.commit()

        result: dict = {
            "status": "created",
            "memory_id": memory_id,
            "instruction_type": inst_type,
            "text": inst_text,
        }

        trigger_conditions = instruction.trigger_conditions
        schedule_config = instruction.schedule_config

        # Create trigger if applicable
        if inst_type == "trigger" and trigger_conditions:
            try:
                from src.models.triggers import Trigger

                async with self._db_factory() as db:
                    trigger_id = f"trg_{ULID()}"
                    trigger = Trigger(
                        trigger_id=trigger_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        name=inst_text[:100],
                        conditions=trigger_conditions,
                        action_type="notify",
                        action_config={},
                        enabled=True,
                        status="active",
                    )
                    db.add(trigger)
                    await db.commit()
                result["trigger_id"] = trigger_id
            except Exception as e:
                logger.warning("Failed to create trigger: %s", e)

        # Create schedule if applicable
        if inst_type == "schedule" and schedule_config:
            try:
                from src.models.schedules import Schedule

                async with self._db_factory() as db:
                    schedule_id = f"sched_{ULID()}"
                    schedule = Schedule(
                        schedule_id=schedule_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        name=inst_text[:100],
                        # schedule_config is a validated ScheduleConfig (its
                        # cron_expr was checked at model-validation time).
                        schedule_type=schedule_config.type,
                        cron_expr=schedule_config.cron_expr,
                        action_type=schedule_config.action_type,
                        action_config=schedule_config.action_config,
                        enabled=True,
                        source="user",
                        priority="medium",
                    )
                    db.add(schedule)
                    await db.commit()
                result["schedule_id"] = schedule_id
            except Exception as e:
                logger.warning("Failed to create schedule: %s", e)

        logger.info("Instruction stored: %s (%s)", inst_text, inst_type)
        return result

    async def _handle_schedule_reminder(
        self,
        reminder_text: str,
        reasoning: str,
        spec: ScheduleReminderInput,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Create a one-shot schedule for a reminder."""
        from src.models.schedules import Schedule

        title = spec.title or reminder_text or reasoning or "Reminder"
        cron_expr = spec.cron_expr or None

        try:
            async with self._db_factory() as db:
                schedule_id = f"sched_{ULID()}"
                schedule = Schedule(
                    schedule_id=schedule_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    name=title[:100],
                    schedule_type="one_shot",
                    cron_expr=cron_expr,
                    action_type="custom_agent_task",
                    action_config={
                        "instructions": f"Remind the user: {title}",
                    },
                    enabled=True,
                    source="user",
                    priority="medium",
                )
                db.add(schedule)
                await db.commit()

            logger.info("Reminder scheduled %s: %s", schedule_id, title)
            return {"status": "created", "schedule_id": schedule_id, "title": title}
        except Exception as e:
            logger.warning("Failed to create reminder schedule: %s", e)
            code, safe_msg, _ = classify(e)
            return {
                "status": "error",
                "error": safe_msg,
                "code": code,
                "correlation_id": get_correlation_id() or new_correlation_id(),
            }

    async def _handle_add_to_brief(self, text: str, user_id: str, workspace_id: str) -> dict:
        """Store a briefing item as a memory so the next briefing includes it."""
        text = text or "Briefing item"
        try:
            async with self._db_factory() as db:
                memory_svc = self._request_services(db).memory_service
                if not memory_svc:
                    return {"status": "error", "error": "Memory service unavailable"}
                memory_id = await memory_svc.store_briefing_memory(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    text=text,
                )
                await db.commit()
            logger.info("Briefing item stored as memory %s: %s", memory_id, text[:80])
            return {"status": "stored", "memory_id": memory_id, "text": text}
        except Exception as e:
            logger.warning("Failed to store briefing item: %s", e)
            code, safe_msg, _ = classify(e)
            return {
                "status": "error",
                "error": safe_msg,
                "code": code,
                "correlation_id": get_correlation_id() or new_correlation_id(),
            }

    async def handle_system_capability(
        self,
        step: PlanStep,
        plan: PlanOutput,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Route system.* capability steps to direct handlers."""
        cap = step.capability

        if cap in ("system.respond", "system.acknowledge"):
            return {}

        known_system_caps = {
            "system.set_goal",
            "system.set_instruction",
            "system.schedule_reminder",
            "system.add_to_brief",
        }
        if cap not in known_system_caps:
            logger.warning("Unknown system capability: %s", cap)
            return {}

        goal_text = step.description or plan.goal
        reasoning = plan.reasoning

        # Execute the system capability
        result: dict = {}
        if cap == "system.set_goal":
            result = await self._handle_set_goal(
                goal_text, reasoning, plan.priority, user_id, workspace_id
            )
        elif cap == "system.set_instruction":
            coerced = _coerce_instruction_input(step.input)
            try:
                spec = SetInstructionStepInput.model_validate(coerced)
            except ValidationError:
                logger.warning("set_instruction input failed validation: %s", step.input)
                return {"status": "error", "error": "invalid set_instruction input"}
            result = await self._handle_set_instruction(
                goal_text, reasoning, spec, user_id, workspace_id
            )
        elif cap == "system.schedule_reminder":
            coerced = _coerce_schedule_reminder_input(step.input)
            try:
                spec = ScheduleReminderInput.model_validate(coerced)
            except ValidationError:
                logger.warning("schedule_reminder input failed validation: %s", step.input)
                return {"status": "error", "error": "invalid schedule_reminder input"}
            result = await self._handle_schedule_reminder(
                goal_text, reasoning, spec, user_id, workspace_id
            )
        elif cap == "system.add_to_brief":
            result = await self._handle_add_to_brief(goal_text, user_id, workspace_id)

        # Audit: record as completed PlanTask + InteractionLog
        if plan.plan_id:
            try:
                from src.models.interaction_log import InteractionLog
                from src.models.plans import PlanTask

                async with self._db_factory() as db:
                    db.add(
                        PlanTask(
                            task_id=f"ptask_{ULID()}",
                            plan_id=plan.plan_id,
                            workspace_id=workspace_id,
                            task_type=cap,
                            input_data=step.input or {"description": step.description},
                            status="completed",
                        )
                    )
                    db.add(
                        InteractionLog(
                            interaction_id=f"ilog_{ULID()}",
                            user_id=user_id,
                            workspace_id=workspace_id,
                            interaction_type=cap,
                            user_message=step.description[:500],
                            assistant_response=str(result)[:500] if result else "completed",
                            metadata_={"plan_step": step.step_id, "actor": "system"},
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to audit system capability step", exc_info=True)

        return result
