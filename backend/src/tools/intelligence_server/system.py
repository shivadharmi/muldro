"""System-action MCP tools (P2.5a): goals, instructions, reminders, briefing items.

These 4 write tools promote the ``system.*`` action capabilities — previously only
Planner-step-strings consumed by ``SystemCapabilityHandler`` — into first-class internal MCP
tools the chat lead can call directly. Each impl calls the SAME service method the handler
uses (``MemoryService.store_goal_memory`` / ``store_instruction_memory`` /
``store_briefing_memory``; a one-shot ``Schedule`` row), so there is NO business-logic
duplication and ``SystemCapabilityHandler`` stays the source of truth for the autonomous /
legacy path (it is untouched).

``system.*`` writes are the user's own memory (reversible, ``self`` blast-radius) and are
ALWAYS-ALLOWED on the chat path — exempt from ``permission_gate`` and ``write_lock`` (D5).
"""

import logging

from fastmcp import Context
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations
from ulid import ULID

from src.integrations.mcp_errors import make_error_response
from src.tools.intelligence_server import _shared
from src.tools.intelligence_server._shared import _get_db, intelligence

logger = logging.getLogger(__name__)


@intelligence.tool(
    tags={"system", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def set_goal(
    title: str,
    ctx: Context,
    priority: str = "medium",
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Record a user goal so Jarvis can track and act toward it."""
    async with _get_db() as db:
        try:
            memory_svc = _shared.request_services(db).memory_service
            if not memory_svc:
                return {"status": "error", "error": "Memory service unavailable"}
            memory_id = await memory_svc.store_goal_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                title=title,
                priority=priority,
            )
            await db.commit()
            return {"status": "created", "memory_id": memory_id, "title": title}
        except Exception as e:  # noqa: BLE001
            logger.error("set_goal failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.tool(
    tags={"system", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def set_instruction(
    instruction_text: str,
    ctx: Context,
    instruction_type: str = "preference",
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Record a standing user instruction / preference so future turns honor it."""
    async with _get_db() as db:
        try:
            memory_svc = _shared.request_services(db).memory_service
            if not memory_svc:
                return {"status": "error", "error": "Memory service unavailable"}
            memory_id = await memory_svc.store_instruction_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                instruction_text=instruction_text,
                instruction_type=instruction_type,
            )
            await db.commit()
            return {
                "status": "created",
                "memory_id": memory_id,
                "instruction_type": instruction_type,
                "text": instruction_text,
            }
        except Exception as e:  # noqa: BLE001
            logger.error("set_instruction failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.tool(
    tags={"system", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def schedule_reminder(
    title: str,
    ctx: Context,
    cron_expr: str = "",
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Create a one-shot reminder for the user."""
    from pydantic import ValidationError

    from src.models.schedules import Schedule
    from src.tools.schemas import ScheduleReminderInput

    # Validate agent-supplied args through the tool's Pydantic input model (its
    # cron_expr field_validator rejects a malformed cron) rather than persisting
    # a raw agent response that later crashes the scheduler.
    try:
        spec = ScheduleReminderInput.model_validate({"title": title, "cron_expr": cron_expr})
    except ValidationError as e:
        return make_error_response(e)

    async with _get_db() as db:
        try:
            schedule_id = f"sched_{ULID()}"
            schedule = Schedule(
                schedule_id=schedule_id,
                user_id=user_id,
                workspace_id=workspace_id,
                name=spec.title[:100],
                schedule_type="one_shot",
                cron_expr=spec.cron_expr or None,
                action_type="custom_agent_task",
                action_config={"instructions": f"Remind the user: {spec.title}"},
                enabled=True,
                source="user",
                priority="medium",
            )
            db.add(schedule)
            await db.commit()
            return {"status": "created", "schedule_id": schedule_id, "title": spec.title}
        except Exception as e:  # noqa: BLE001
            logger.error("schedule_reminder failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.tool(
    tags={"system", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def add_to_brief(
    text: str,
    ctx: Context,
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Add an item to the user's next daily briefing."""
    async with _get_db() as db:
        try:
            memory_svc = _shared.request_services(db).memory_service
            if not memory_svc:
                return {"status": "error", "error": "Memory service unavailable"}
            memory_id = await memory_svc.store_briefing_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                text=text,
            )
            await db.commit()
            return {"status": "stored", "memory_id": memory_id, "text": text}
        except Exception as e:  # noqa: BLE001
            logger.error("add_to_brief failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)
