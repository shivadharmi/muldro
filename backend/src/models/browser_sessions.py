"""Browser automation models — action audit log for replay."""

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class BrowserAction(Base, TimestampMixin):
    __tablename__ = "browser_actions"

    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # navigate/click/fill/screenshot/extract
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending/success/failed
    screenshot_before: Mapped[str | None] = mapped_column(String(64), nullable=True)  # artifact_id
    screenshot_after: Mapped[str | None] = mapped_column(String(64), nullable=True)  # artifact_id
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_browser_actions_session", "session_id", "created_at"),)
