"""One "not this one" from the founder, against one thing, per person.

Dismissing a card says two different things at once, and neither implies the
other. To the ranker it says "less of this KIND", which is what
`engagement_history` records against `(source, event_type)`. To the workspace
it says "not THIS one", and nothing recorded that: the feed is a pure
projection of live domain rows, so a dismissed card came straight back on the
next poll. This row is the second fact.

WHAT IT HIDES IS WHAT THE FOUNDER SAW, not the thing for ever. `dismissed_at`
is stamped at the moment they looked, and the feed hides the unit only while
`frame.updated_at` is at or before it. A dismissed thread that gets a reply has
moved past that instant and comes back, because a reply is new information; the
same thread untouched stays gone. That is why this is a timestamp and not a
boolean — a boolean would bury a thread the moment it went quiet and keep it
buried through everything that happened next.

PER USER, not per workspace. Two members read the same rows and are not
answerable for the same things, so one of them clearing their view must never
clear the other's.

NO `expires_at`. Nothing here is superseded by a clock; it is superseded by the
thing changing, which `frame.updated_at` already names.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class UnitDismissal(Base, TimestampMixin):
    """One dismissed thing, per workspace, per user."""

    __tablename__ = "unit_dismissals"

    dismissal_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("dsm")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    # The view is per person: a workspace-wide dismissal would let one member
    # decide what another one never sees.
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Text, not a fixed varchar, for the same reason `unit_bodies.frame_key`
    # is: the key is f"{source}:{entity_type}:{entity_id}" and an external
    # entity_id is opaque and unbounded (a Google Calendar recurring-instance
    # id can reach ~1024 chars). A width that fits the common case turns the
    # uncommon one into an insert failure at dismiss time.
    frame_key: Mapped[str] = mapped_column(Text, nullable=False)

    # WHEN THEY LOOKED. The whole re-surfacing rule is a comparison against
    # this, so a second dismissal must overwrite it rather than add a row —
    # hence the unique constraint below.
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "frame_key", name="uq_unit_dismissal_ws_user_frame"
        ),
        # The feed reads every dismissal for one person on every request.
        Index("ix_unit_dismissals_workspace_user", "workspace_id", "user_id"),
    )
