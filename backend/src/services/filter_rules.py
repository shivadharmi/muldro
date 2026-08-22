"""Reading the founder's confirmed filters, and matching mail against them.

Kept apart from the model so the matching rules are testable without a
database, and apart from `triage` so the one question this answers — "did the
founder already tell us to keep this quiet?" — has a name.
"""

import logging
from collections.abc import Mapping
from email.utils import parseaddr
from typing import Any

from sqlalchemy import select

from src.models.filter_rule import FilterRule

logger = logging.getLogger(__name__)

__all__ = [
    "SenderRules",
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
