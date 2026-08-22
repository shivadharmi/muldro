"""Notice a filter worth having, and ask the founder for it (hourly).

muldro can see which senders it keeps setting aside. Acting on that alone would
be taking control for convenience; the soul's sequence is observe, interpret,
surface selectively, PROPOSE BEFORE OVERCOMMITTING, and only then act within
established boundaries. So the pattern becomes one card, the founder answers it
once, and only then does a rule exist.

Hourly rather than per poll. The evidence is a fortnight of triage verdicts and
moves slowly, `find_sender_candidates` reads the whole window each time, and
the proposal itself is throttled to one open card with a cooldown after either
answer — so a faster cadence would buy nothing and cost a scan every 30s.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.models.database import get_session_factory
from src.models.users import WorkspaceMember

logger = logging.getLogger(__name__)

# ~1 hour at a 30s poll. Slow enough to be free, fast enough that a pattern is
# noticed the same day it forms rather than the next.
FILTER_PROPOSAL_TICK_EVERY = 120


class FilterProposalTickMixin:
    """Proposes sender filters the founder can accept in one decision."""

    async def _tick_filter_proposals(self, factory=None) -> None:
        if getattr(self, "_tick_count", 0) % FILTER_PROPOSAL_TICK_EVERY != 0:
            return

        from src.services.filter_proposals import (
            create_filter_proposal,
            find_sender_candidates,
            open_or_recent_proposal,
        )

        now = datetime.now(timezone.utc)
        try:
            factory = factory or get_session_factory()
            async with factory() as db:
                members = list((await db.execute(select(WorkspaceMember))).scalars().all())
                for member in members:
                    workspace_id = getattr(member, "workspace_id", "")
                    user_id = getattr(member, "user_id", "")
                    if not workspace_id or not user_id:
                        continue
                    # Checked per workspace, not once: one founder having an
                    # open proposal must not silence everyone else's.
                    if await open_or_recent_proposal(db, workspace_id=workspace_id, now=now):
                        continue
                    candidates = await find_sender_candidates(
                        db, workspace_id=workspace_id, user_id=user_id, now=now
                    )
                    if not candidates:
                        continue
                    await create_filter_proposal(
                        db,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        candidates=candidates,
                        now=now,
                    )
                    logger.info(
                        "filter_proposal_offered workspace=%s senders=%d",
                        workspace_id,
                        len(candidates),
                    )
                await db.commit()
        except Exception:
            # A proposal is never worth a tick. Everything else in this cycle
            # still runs, and the next hour tries again.
            logger.warning("filter_proposal_tick_failed", exc_info=True)
