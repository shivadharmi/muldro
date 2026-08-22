"""Reading the founder's confirmed filters, and matching mail against them.

Kept apart from the model so the matching rules are testable without a
database, and apart from `triage` so the one question this answers — "did the
founder already tell us to keep this quiet?" — has a name.
"""

import logging
from collections.abc import Mapping
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from sqlalchemy import select

from src.models.events import NormalizedEvent
from src.models.filter_rule import FilterRule

logger = logging.getLogger(__name__)

__all__ = [
    "SenderRules",
    "apply_approved_proposal",
    "load_all_sender_rules",
    "revoke_rule",
    "load_sender_rules",
    "matching_rule_id",
    "normalize_sender",
    "sender_of",
]

# `{(source, sender_address): rule_id}`. The rule id travels with the match
# because every filtered event is stamped with it: without that, a verdict
# frozen at ingest outlives the rule that caused it, and "why is this hidden?"
# has no answer.
SenderRules = Mapping[tuple[str, str], str]


def normalize_sender(value: Any) -> str:
    """One canonical form for an address, used on both write and read.

    Case-folded because addresses are compared, not displayed, and the domain
    half is case-insensitive by RFC. Applied when a rule is STORED as well, so
    matching is a dict lookup rather than a per-event parse.
    """
    if not isinstance(value, str):
        return ""
    _, address = parseaddr(value)
    if "@" not in address:
        address = value
    return address.strip().strip("<>").lower()


def sender_of(event: Any) -> str:
    """The address an event came from, whichever pipeline stage it is at.

    A pre-ingest RawEvent carries `actor` (a dict); a stored NormalizedEvent
    carries `actor_entities` (a LIST of dicts in production, despite the model
    annotating `dict | None`). The same split `view/frame.py::event_actor_name`
    exists for, and for the same reason: a value that can be either shape must
    be read in one place or the two readers will disagree.

    Returns "" when there is no address — which never matches a rule, because
    a rule is an address.
    """
    actor = getattr(event, "actor", None)
    if actor is None:
        actor = getattr(event, "actor_entities", None)
    if isinstance(actor, list):
        actor = actor[0] if actor else None
    if not isinstance(actor, dict):
        return ""
    return normalize_sender(actor.get("email"))


def matching_rule_id(event: Any, rules: SenderRules) -> str | None:
    """The id of the rule that says to keep this quiet, or None.

    Exact address match only. No domain wildcards and no prefix matching: a
    rule the founder confirmed said one address, and widening it here would
    exercise an authority they did not grant — quietly, and on mail they never
    saw.
    """
    if not rules:
        return None
    sender = sender_of(event)
    if not sender:
        return None
    source = getattr(event, "source", "")
    if not isinstance(source, str) or not source:
        return None
    return rules.get((source, sender))


async def load_sender_rules(db: Any, *, workspace_id: str) -> dict[tuple[str, str], str]:
    """Every live sender rule for this workspace. Never raises.

    A read failure costs the FILTERS, never the ingest: returning nothing means
    everything is triaged as it was before any rule existed, which is the safe
    direction. Failing the other way would silently drop mail on a database
    hiccup.
    """
    if not workspace_id:
        return {}
    try:
        result = await db.execute(
            select(FilterRule).where(
                FilterRule.workspace_id == workspace_id,
                FilterRule.match_kind == "sender",
                FilterRule.enabled.is_(True),
                FilterRule.revoked_at.is_(None),
            )
        )
        rows = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001 - a filter outage must not cost ingest
        logger.warning("filter_rules_read_failed workspace=%s error=%s", workspace_id, exc)
        return {}
    return {(r.source, r.match_value): r.rule_id for r in rows}


async def apply_approved_proposal(db: Any, approval: Any) -> list[str]:
    """Write the rules a founder just confirmed. Returns the new rule ids.

    Reads the addresses off the approval's `artifact_refs`, never off fresh
    evidence. The founder answered a specific card naming specific senders, and
    re-deriving the list at confirmation time would create rules for whatever
    the inbox looks like NOW — the same reason a prepared action replays its
    recorded payload instead of re-running the agent that produced it.

    One rule per address, not one rule for the batch. A single yes is
    convenient to give and must still be revocable piece by piece.

    Idempotent: a second confirmation of the same approval adds nothing,
    because the unique constraint already holds one rule per
    (workspace, source, kind, value) and an existing rule is left alone rather
    than duplicated or re-dated.
    """
    refs = getattr(approval, "artifact_refs", None) or {}
    senders = refs.get("senders") or []
    if not senders:
        return []

    existing = await load_all_sender_rules(db, workspace_id=approval.workspace_id)
    created: list[str] = []
    for entry in senders:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        address = normalize_sender(entry.get("address"))
        if not source or not address or (source, address) in existing:
            continue
        rule = FilterRule(
            workspace_id=approval.workspace_id,
            user_id=approval.user_id,
            source=source,
            match_kind="sender",
            match_value=address,
            created_from_approval_id=approval.approval_id,
        )
        db.add(rule)
        await db.flush()
        created.append(rule.rule_id)
    logger.info(
        "filter_rules_created workspace=%s approval=%s rules=%d",
        approval.workspace_id,
        getattr(approval, "approval_id", "?"),
        len(created),
    )
    return created


async def load_all_sender_rules(db: Any, *, workspace_id: str) -> set[tuple[str, str]]:
    """Every sender rule, live or revoked. Used to avoid re-creating one the
    founder has already answered — in either direction."""
    try:
        rows = (
            (
                await db.execute(
                    select(FilterRule).where(
                        FilterRule.workspace_id == workspace_id,
                        FilterRule.match_kind == "sender",
                    )
                )
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("filter_rules_read_failed workspace=%s error=%s", workspace_id, exc)
        return set()
    return {(r.source, r.match_value) for r in rows}


async def revoke_rule(db: Any, *, workspace_id: str, rule_id: str, now: datetime) -> int:
    """Turn a rule off and RELEASE the mail it hid. Returns rows released.

    Revoking is not enough on its own. The triage verdict was frozen into
    `importance_signals` at ingest, so without this the mail would stay
    unactionable — and therefore folded — for ever, with the rule that caused
    it already gone. That is the failure `filtered_by` exists to make fixable.

    The row is kept rather than deleted: a deleted rule loses the evidence of
    what it once hid, and the founder may want it back.
    """
    rule = (
        (
            await db.execute(
                select(FilterRule).where(
                    FilterRule.workspace_id == workspace_id,
                    FilterRule.rule_id == rule_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if rule is None:
        return 0
    rule.enabled = False
    rule.revoked_at = now

    released = 0
    events = (
        (
            await db.execute(
                select(NormalizedEvent).where(
                    NormalizedEvent.workspace_id == workspace_id,
                    NormalizedEvent.importance_signals["filtered_by"].astext == rule_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for event in events:
        signals = dict(event.importance_signals or {})
        signals.pop("filtered_by", None)
        # Back to unclassified, NOT to some guessed verdict. The next poll
        # re-triages it honestly; asserting `actionable=True` here would be
        # muldro deciding on the founder's behalf in the other direction.
        signals["actionable"] = None
        signals["triage_origin"] = "default"
        event.importance_signals = signals
        released += 1
    await db.flush()
    logger.info(
        "filter_rule_revoked workspace=%s rule=%s released=%d", workspace_id, rule_id, released
    )
    return released
