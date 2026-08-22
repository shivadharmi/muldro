"""Noticing a filter worth having, and asking the founder for it.

The soul's initiative sequence is observe -> interpret -> surface selectively ->
PROPOSE BEFORE OVERCOMMITTING -> act within established boundaries. muldro can
see which senders it keeps setting aside; what it must not do is act on that on
its own. So the pattern becomes a proposal, the founder answers it once, and
only then does a rule exist.

ONE proposal, batched. Six separate approval cards to silence six bank alerts
would be the noise problem wearing a different hat. The cost is that a single
yes grants several authorities at once, which is why the card names every
address it would quiet and why each becomes its own revocable rule rather than
one lump.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from ulid import ULID

from src.models.approvals import Approval
from src.models.events import NormalizedEvent
from src.models.filter_rule import FilterRule
from src.services.filter_rules import sender_of

logger = logging.getLogger(__name__)

__all__ = [
    "FILTER_PROPOSAL_TYPE",
    "MIN_EVENTS_TO_PROPOSE",
    "SenderCandidate",
    "create_filter_proposal",
    "find_sender_candidates",
    "open_or_recent_proposal",
]

FILTER_PROPOSAL_TYPE = "filter_proposal"

# How much repetition makes a pattern. Below this it is a coincidence, and
# proposing on a coincidence teaches the founder to dismiss proposals.
MIN_EVENTS_TO_PROPOSE = 5

# The window the evidence is drawn from. Matches the feed's own look-back, so
# what muldro proposes about is what the founder could actually have seen.
PROPOSAL_WINDOW_DAYS = 14

# Ceiling per card. A proposal the founder cannot read in one glance is not a
# proposal, it is a form.
MAX_SENDERS_PER_PROPOSAL = 8

# After an answer — either answer — wait. Re-asking about senders the founder
# just declined is nagging, and nagging is how a proposal channel dies.
PROPOSAL_COOLDOWN_DAYS = 7


@dataclass(frozen=True)
class SenderCandidate:
    """One address muldro would quiet, and the evidence for it."""

    source: str
    address: str
    event_count: int
    sample_subject: str


async def open_or_recent_proposal(db: Any, *, workspace_id: str, now: datetime) -> bool:
    """Whether a proposal is already waiting, or was recently answered.

    One open proposal at a time. Two competing cards asking about overlapping
    senders would let the founder grant the same authority twice and revoke it
    once.
    """
    since = now - timedelta(days=PROPOSAL_COOLDOWN_DAYS)
    try:
        result = await db.execute(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.approval_type == FILTER_PROPOSAL_TYPE,
                Approval.created_at >= since,
            )
        )
        return result.scalars().first() is not None
    except Exception as exc:  # noqa: BLE001 - never propose on a failed check
        logger.warning("filter_proposal_check_failed workspace=%s error=%s", workspace_id, exc)
        return True


async def find_sender_candidates(
    db: Any, *, workspace_id: str, user_id: str, now: datetime
) -> list[SenderCandidate]:
    """Senders worth proposing a filter for. Never raises; [] on any doubt.

    The evidence is muldro's OWN triage record, not a model asked afresh: every
    event from this sender inside the window was judged unactionable, and there
    were enough of them to be a habit rather than an accident.

    A single actionable event disqualifies the sender outright. A counterparty
    who has once needed the founder can need them again, and the cost of a
    wrong rule is mail they never see.
    """
    since = now - timedelta(days=PROPOSAL_WINDOW_DAYS)
    try:
        rows = list(
            (
                await db.execute(
                    select(NormalizedEvent).where(
                        NormalizedEvent.workspace_id == workspace_id,
                        NormalizedEvent.user_id == user_id,
                        NormalizedEvent.occurred_at >= since,
                    )
                )
            )
            .scalars()
            .all()
        )
        ruled = {
            (r.source, r.match_value)
            for r in (
                await db.execute(select(FilterRule).where(FilterRule.workspace_id == workspace_id))
            )
            .scalars()
            .all()
        }
    except Exception as exc:  # noqa: BLE001 - a proposal is never worth an outage
        logger.warning("filter_candidates_read_failed workspace=%s error=%s", workspace_id, exc)
        return []

    tally: dict[tuple[str, str], list[NormalizedEvent]] = {}
    for row in rows:
        address = sender_of(row)
        if not address:
            continue
        tally.setdefault((row.source, address), []).append(row)

    candidates: list[SenderCandidate] = []
    for (source, address), events in tally.items():
        # Already decided, in either direction. A revoked rule is an answer too:
        # re-proposing it would be asking the founder to repeat themselves.
        if (source, address) in ruled:
            continue
        if len(events) < MIN_EVENTS_TO_PROPOSE:
            continue
        verdicts = [(e.importance_signals or {}).get("actionable") for e in events]
        if not all(v is False for v in verdicts):
            continue
        newest = max(events, key=lambda e: e.occurred_at)
        candidates.append(
            SenderCandidate(
                source=source,
                address=address,
                event_count=len(events),
                sample_subject=(newest.title or "").strip(),
            )
        )

    # Loudest first: the sender costing the most attention is the one whose
    # rule buys the most, and the cap should keep those rather than whichever
    # happened to hash first.
    candidates.sort(key=lambda c: (-c.event_count, c.address))
    return candidates[:MAX_SENDERS_PER_PROPOSAL]


def proposal_title(candidates: Sequence[SenderCandidate]) -> str:
    """What the card says. Code-authored: this is muldro's own voice about its
    own records, and no external text reaches it."""
    total = sum(c.event_count for c in candidates)
    senders = len(candidates)
    return (
        f"Keep mail from {senders} sender{'s' if senders != 1 else ''} quiet? "
        f"{total} message{'s' if total != 1 else ''}, none needed you."
    )


async def create_filter_proposal(
    db: Any,
    *,
    workspace_id: str,
    user_id: str,
    candidates: Sequence[SenderCandidate],
    now: datetime,
) -> Approval | None:
    """Record the proposal the founder will answer. None when there is nothing
    to ask about.

    The candidates travel on `artifact_refs` because that is what the
    confirmation replays: approving must create rules for exactly the addresses
    the founder READ, never for whatever the evidence looks like by the time
    they answer. The same reason a prepared action carries its payload rather
    than being re-derived.
    """
    if not candidates:
        return None
    approval = Approval(
        approval_id=f"apr_{ULID()}",
        user_id=user_id,
        workspace_id=workspace_id,
        execution_id="",
        approval_type=FILTER_PROPOSAL_TYPE,
        title=proposal_title(candidates),
        summary="\n".join(
            f"{c.address} — {c.event_count} messages, e.g. {c.sample_subject}"[:300]
            for c in candidates
        ),
        artifact_refs={
            "senders": [
                {"source": c.source, "address": c.address, "event_count": c.event_count}
                for c in candidates
            ]
        },
        risk_level="low",
        status="pending",
    )
    db.add(approval)
    await db.flush()
    logger.info(
        "filter_proposal_created workspace=%s senders=%d",
        workspace_id,
        len(candidates),
    )
    return approval
