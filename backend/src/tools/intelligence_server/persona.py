"""Persona/presentation-domain MCP tools: preferences, briefing."""

import logging

from fastmcp import Context
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations

from src.integrations.mcp_errors import make_error_response
from src.tools.intelligence_server import _shared
from src.tools.intelligence_server._shared import _get_db, intelligence

logger = logging.getLogger(__name__)


@intelligence.tool(
    tags={"persona", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def extract_preferences(
    user_id: str,
    source_text: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Extract user preferences from interaction text.

    The Persona agent calls this to store learned preferences as memories.
    source_text: description of the interaction to analyze
    """
    async with _get_db() as db:
        try:
            from src.services.memory_service import MemoryService

            # Create a MemoryService bound to THIS session so the
            # commit below actually persists extracted preferences.
            memory_service = MemoryService(
                settings=_shared._settings,
                db=db,
                vector_store=_shared._services.vector_store,
            )

            memory_ids = await memory_service.extract_preferences(
                user_id=user_id,
                source_text=source_text,
                source_event_ids=[],
                workspace_id=workspace_id,
            )
            await db.commit()
            return {
                "status": "ok",
                "memories_created": len(memory_ids),
                "memory_ids": memory_ids,
            }
        except Exception as e:
            logger.error("extract_preferences failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.tool(
    tags={"presenter", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_briefing(
    user_id: str,
    ctx: Context,
    date: str = "today",
    workspace_id: str = "",
) -> dict:
    """Generate or fetch the daily briefing.

    date: 'today' or ISO date string (YYYY-MM-DD)
    """
    async with _get_db():
        try:
            from datetime import date as date_type

            await ctx.report_progress(0, 3, "Loading briefing data...")
            briefing_date = date_type.today() if date == "today" else date_type.fromisoformat(date)
            presenter = _shared._services.presenter
            await ctx.report_progress(1, 3, "Generating briefing...")
            briefing = await presenter.generate_briefing(
                user_id, briefing_date, workspace_id=workspace_id
            )
            await ctx.report_progress(3, 3, "Briefing ready")
            return {
                "status": "ok",
                "briefing_id": briefing.briefing_id,
                "briefing_date": str(briefing.briefing_date),
                "headline": briefing.headline,
                "top_priorities": briefing.top_priorities,
                "changes_since_last": briefing.changes_since_last,
                "pending_approvals": briefing.pending_approvals,
                "recommended_actions": briefing.recommended_actions,
                "full_text": briefing.full_text,
            }
        except Exception as e:
            logger.error("get_briefing failed: %s", e, exc_info=True)
            return make_error_response(e)


@intelligence.tool(
    tags={"persona", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def store_preference(
    user_id: str,
    text: str,
    ctx: Context,
    confidence: float = 0.5,
    source_text: str = "",
    workspace_id: str = "",
) -> dict:
    """Store a user preference extracted from interactions."""
    async with _get_db() as db:
        try:
            from src.services.memory_service import MemoryService

            # Create a MemoryService bound to THIS session so the
            # commit below actually persists the preference.
            memory_svc = MemoryService(
                settings=_shared._settings,
                db=db,
                vector_store=_shared._services.vector_store,
            )

            mid = await memory_svc.store_instruction_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                instruction_text=text,
                instruction_type="preference",
            )
            await db.commit()
            await ctx.info(f"Stored preference: {text[:80]} (confidence={confidence})")
            return {"status": "stored", "memory_id": mid, "confidence": confidence}
        except Exception as e:
            logger.error("store_preference failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)
