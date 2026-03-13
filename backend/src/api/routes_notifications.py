"""Notification endpoints — send outbound notifications to user channels."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.schemas import NotificationRequest, NotificationResponse
from src.config.settings import Settings, get_settings
from src.services.notification_service import NotificationService

router = APIRouter()


@router.post("/v1/notifications/send", response_model=NotificationResponse)
async def send_notification(
    req: NotificationRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Send a notification to the user via the specified channel."""
    service = NotificationService(settings=settings, db=db)
    result = await service.notify(
        user_id=user_id,
        title=req.title,
        body=req.body,
        channel=req.channel,
        urgency=req.urgency,
    )
    return NotificationResponse(**result)
