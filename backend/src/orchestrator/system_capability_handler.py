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

from ulid import ULID

from src.contracts import PlanOutput, PlanStep
from src.errors import classify, new_correlation_id
from src.middleware.observability import get_correlation_id
from src.orchestrator.services import ServiceContainer

logger = logging.getLogger(__name__)


class SystemCapabilityHandler:
    """Route and execute ``system.*`` capability steps against the data layer."""

    def __init__(self, db_factory, services: ServiceContainer):
        self._db_factory = db_factory
        self._services = services

    async def _handle_set_goal(
        self,
        goal_text: str,
        reasoning: str,
        priority: str,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Store a goal as a memory via MemoryService."""
        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        title = goal_text or reasoning or "Untitled goal"
        memory_id = await memory_svc.store_goal_memory(
            user_id=user_id,
            workspace_id=workspace_id,
            title=title,
            priority=priority,
        )
        logger.info("Goal stored as memory %s: %s", memory_id, title)
        return {"status": "created", "memory_id": memory_id, "title": title}

    async def _handle_set_instruction(
        self,
        instruction_text: str,
        reasoning: str,
        instruction: dict,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Handle set_instruction: create trigger/schedule/preference memory."""
        if not instruction:
            return {"status": "error", "error": "No instruction spec provided"}

        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        inst_text = instruction.get("instruction_text", instruction_text)
        inst_type = instruction.get("instruction_type", "preference")

        # Store as a preference memory via public API
        memory_id = await memory_svc.store_instruction_memory(
            user_id=user_id,
            workspace_id=workspace_id,
            instruction_text=inst_text,
            instruction_type=inst_type,
        )

        result: dict = {
            "status": "created",
            "memory_id": memory_id,
            "instruction_type": inst_type,
            "text": inst_text,
        }

        trigger_conditions = instruction.get("trigger_conditions")
        schedule_config = instruction.get("schedule_config")

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
                        schedule_type=schedule_config.get("type", "recurring"),
                        cron_expr=schedule_config.get("cron_expr"),
                        action_type=schedule_config.get("action_type", "custom_agent_task"),
                        action_config=schedule_config.get("action_config", {}),
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
        tasks: list[dict],
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Create a one-shot schedule for a reminder."""
        from src.models.schedules import Schedule

        title = reminder_text or reasoning or "Reminder"
        # Extract timing from tasks if available
        schedule_config: dict = {}
        if tasks:
            schedule_config = tasks[0].get("input_data") or {}

        try:
            async with self._db_factory() as db:
                schedule_id = f"sched_{ULID()}"
                schedule = Schedule(
                    schedule_id=schedule_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    name=title[:100],
                    schedule_type="one_shot",
                    cron_expr=schedule_config.get("cron_expr"),
                    action_type="custom_agent_task",
                    action_config={
                        "instructions": f"Remind the user: {title}",
                        **schedule_config,
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
        memory_svc = self._services.memory_service
        if not memory_svc:
            return {"status": "error", "error": "Memory service unavailable"}

        text = text or "Briefing item"
        try:
            memory_id = await memory_svc.store_briefing_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                text=text,
            )
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
            instruction = step.input.get("instruction", {})
            result = await self._handle_set_instruction(
                goal_text, reasoning, instruction, user_id, workspace_id
            )
        elif cap == "system.schedule_reminder":
            tasks = step.input.get("tasks", [])
            result = await self._handle_schedule_reminder(
                goal_text, reasoning, tasks, user_id, workspace_id
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
