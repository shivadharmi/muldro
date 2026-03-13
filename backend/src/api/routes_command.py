"""Command endpoint — the primary entry point from OpenClaw's jarvis_command tool."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.schemas import CommandRequest, CommandResponse
from src.config.settings import Settings, get_settings
from src.services.planner import Planner
from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/jarvis/command", response_model=CommandResponse)
async def handle_command(
    req: CommandRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Process a user command through the Jarvis pipeline.

    Flow: parse intent → retrieve context → plan → execute/draft → respond.
    """
    world_model = WorldModel(settings=settings, db=db)
    planner = Planner(settings=settings, db=db, world_model=world_model)

    try:
        plan = await planner.plan_for_command(
            command=req.command,
            user_id=user_id,
            context=req.context,
        )
    except Exception:
        logger.exception("Planner failed for command: %s", req.command)
        return CommandResponse(
            decision="error",
            summary="Sorry, I had trouble processing that. Please try again.",
        )

    return CommandResponse(
        plan_id=plan.plan_id,
        decision=plan.decision,
        summary=plan.goal,
    )
