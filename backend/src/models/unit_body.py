"""The stored body of one perception Unit.

A view is a pure function of a domain row, and no view reads a cache. A body
costs a model call, so it cannot be recomputed on every feed refresh —
therefore it must BE a row. The `Unit` is then a pure projection of two row
sets: the frame and the quotes come from the events, the body from here.

TWO ROWS, NOT ONE. A chat answer stays a `Finding`; a perception body lands
here. The two look alike, and they diverge on exactly the fields that carry
the design: a Finding's `derivation` names which tools to re-run, which is
empty for a body whose derivation is its events; and a Finding goes stale on a
TIMER while a body goes stale STRUCTURALLY — `event_ids` changed, so a new
message arrived and the prose no longer describes the thing.

NO `claim`, `lede`, `summary` OR `preview` COLUMN, ever. The lede is paragraph
1 of `body`, computed on read. A stored lede is a second projection of one
string, free to drift from the string it summarises — the "same sentence at two
lengths" defect this rebuild removed, reintroduced as a schema column.

NO `expires_at`. A body is superseded by the next message, never by a clock.
"""

from datetime import datetime

from sqlalchemy import ARRAY, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin
from src.models.ids import generate_id


class UnitBody(Base, TimestampMixin):
    """One model-authored markdown body per thing, per workspace."""

    __tablename__ = "unit_bodies"

    unit_body_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("ubody")
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    # Text, not a fixed varchar: the key is f"{source}:{entity_type}:{entity_id}"
    # and an external entity_id is opaque and unbounded (a Google Calendar
    # recurring-instance id can reach ~1024 chars). `normalized_events` makes the
    # same choice for `idempotency_key`, which embeds the same value, and carries
    # the same composite unique constraint over it.
    frame_key: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # WHAT IT WAS WRITTEN OVER. Staleness is set inequality against the events
    # the current poll grouped under this key — structural, not a timer.
    event_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "frame_key", name="uq_unit_bodies_ws_frame"),
        Index("ix_unit_bodies_workspace", "workspace_id"),
    )
