"""WebhookSubscription — tracks push/webhook registrations per connector.

Each subscription represents a registered webhook with an external service
(e.g., Google Push Notifications, GitHub webhooks, Slack Events API).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.ids import generate_id


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    subscription_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("whsub")
    )
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)  # gmail, github, slack
    resource_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # mailbox, repository, channel
    resource_id: Mapped[str] = mapped_column(
        String(256), nullable=False
    )  # e.g., repo full_name, channel id
    external_id: Mapped[str | None] = mapped_column(String(256))  # webhook ID from the provider
    callback_url: Mapped[str] = mapped_column(String(512), nullable=False)
    secret: Mapped[str | None] = mapped_column(String(256))  # HMAC secret for verification
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active"
    )  # active, paused, expired, failed
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_count: Mapped[int] = mapped_column(default=0, server_default="0")
    consecutive_failures: Mapped[int] = mapped_column(default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_webhook_subs_ws", "workspace_id"),
        Index("ix_webhook_subs_ws_provider", "workspace_id", "provider"),
        Index("ix_webhook_subs_ws_status", "workspace_id", "status"),
        Index("ix_webhook_subs_external", "external_id"),
    )
