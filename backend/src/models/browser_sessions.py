"""Browser automation models — session tracking and action audit log."""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class BrowserSession(Base, TimestampMixin):
    __tablename__ = "browser_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="idle"
    )  # idle/active/recording
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    screenshot_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_browser_sessions_user_status", "user_id", "status"),)


class BrowserAction(Base, TimestampMixin):
    __tablename__ = "browser_actions"

    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # navigate/click/fill/screenshot/extract
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending/success/failed
    screenshot_before: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # artifact_id
    screenshot_after: Mapped[str | None] = mapped_column(String(64), nullable=True)  # artifact_id

    __table_args__ = (Index("ix_browser_actions_session", "session_id", "created_at"),)
