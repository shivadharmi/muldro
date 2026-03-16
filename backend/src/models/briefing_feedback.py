from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class BriefingFeedback(Base, TimestampMixin):
    """Tracks user feedback on briefings and individual briefing items.

    Each row = one feedback signal. Multiple signals per briefing are expected
    (overall rating + per-item interactions).
    """

    __tablename__ = "briefing_feedback"

    feedback_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    briefing_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )

    # "rating" | "item_acted_on" | "item_dismissed" | "follow_up_asked"
    feedback_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Overall rating (1-5) when feedback_type="rating"
    rating: Mapped[int | None] = mapped_column(Integer)

    # Which briefing item the user interacted with (index into top_priorities, etc.)
    item_section: Mapped[str | None] = mapped_column(String(64))  # e.g. "top_priorities"
    item_index: Mapped[int | None] = mapped_column(Integer)
    item_title: Mapped[str | None] = mapped_column(String(512))

    # Free-form comment from user
    comment: Mapped[str | None] = mapped_column(Text)

    # Structured extra_data (e.g. which button pressed, follow-up query)
    extra_data: Mapped[dict | None] = mapped_column(JSONB)

    # Importance weight for learning (higher = stronger signal)
    signal_weight: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (
        Index("ix_bf_user_briefing", "user_id", "briefing_id"),
        Index("ix_bf_user_type", "user_id", "feedback_type"),
    )
