"""Command endpoint — the primary entry point from OpenClaw's jarvis_command tool."""

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.schemas import CommandRequest, CommandResponse

router = APIRouter()


@router.post("/v1/jarvis/command", response_model=CommandResponse)
async def handle_command(
    req: CommandRequest,
    user_id: str = Depends(get_current_user),
):
    """Process a user command through the Jarvis pipeline.

    Flow: parse intent → retrieve context → plan → execute/draft → respond.
    This is a stub — the planner service will be wired in next phase.
    """
    # TODO: Wire to planner service
    return CommandResponse(
        decision="acknowledged",
        summary=f"Received command: {req.command}. Jarvis planner not yet connected.",
    )
