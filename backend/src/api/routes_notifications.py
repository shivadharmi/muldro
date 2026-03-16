"""Notification endpoints — list, read, dismiss notifications."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.models.notifications import Notification

logger = logging.getLogger(__name__)

router = APIRouter()


class NotificationResponse(BaseModel):
    notification_id: str
    channel: str
    title: str
    body: str | None = None
    payload_json: dict | None = None
    priority_score: float = 0.5
    status: str
    sent_at: datetime | None = None
    read_at: datetime | None = None
    created_at: datetime | None = None


@router.get("/v1/notifications", response_model=list[NotificationResponse])
async def list_notifications(
    status: str | None = None,
    limit: int = 50,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """List notifications for the user."""
    stmt = select(Notification).where(Notification.user_id == user_id)
    if status:
        stmt = stmt.where(Notification.status == status)
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    notifications = result.scalars().all()
    return [
        NotificationResponse(
            notification_id=n.notification_id,
            channel=n.channel,
            title=n.title,
            body=n.body,
            payload_json=n.payload_json,
            priority_score=n.priority_score,
            status=n.status,
            sent_at=n.sent_at,
            read_at=n.read_at,
            created_at=n.created_at,
        )
        for n in notifications
    ]


@router.post("/v1/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_read(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Mark a notification as read."""
    result = await db.execute(
        select(Notification).where(
            Notification.notification_id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    notif.status = "read"
    notif.read_at = datetime.now(timezone.utc)
    await db.commit()

    return NotificationResponse(
        notification_id=notif.notification_id,
        channel=notif.channel,
        title=notif.title,
        body=notif.body,
        payload_json=notif.payload_json,
        priority_score=notif.priority_score,
        status=notif.status,
        sent_at=notif.sent_at,
        read_at=notif.read_at,
        created_at=notif.created_at,
    )


@router.post("/v1/notifications/{notification_id}/dismiss", response_model=NotificationResponse)
async def dismiss_notification(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Dismiss a notification."""
    result = await db.execute(
        select(Notification).where(
            Notification.notification_id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    notif.status = "dismissed"
    await db.commit()

    return NotificationResponse(
        notification_id=notif.notification_id,
        channel=notif.channel,
        title=notif.title,
        body=notif.body,
        payload_json=notif.payload_json,
        priority_score=notif.priority_score,
        status=notif.status,
        sent_at=notif.sent_at,
        read_at=notif.read_at,
        created_at=notif.created_at,
    )
