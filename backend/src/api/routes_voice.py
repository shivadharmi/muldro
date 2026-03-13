"""Voice endpoints — TTS-friendly response conversion."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.schemas import VoiceRequest, VoiceResponse
from src.config.settings import Settings, get_settings
from src.services.voice_service import VoiceService

router = APIRouter()


@router.post("/v1/voice/convert", response_model=VoiceResponse)
async def convert_to_voice(
    req: VoiceRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Convert structured content to voice-friendly spoken text."""
    service = VoiceService(settings=settings, db=db)
    result = await service.to_voice(req.content, req.content_type)
    return VoiceResponse(
        spoken_text=result.get("spoken_text", ""),
        duration_hint=result.get("duration_hint", "medium"),
    )
