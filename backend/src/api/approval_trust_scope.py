"""What a rejection is evidence about.

Trust is evidence about a CAPABILITY, and not every approval names one. This
lives apart from the route because it is a rule about the trust ladder rather
than about HTTP — and because `routes_approvals.py` is at its size cap, which
is the better reason to have looked for the seam.
"""

from src.integrations.capabilities import CAPABILITY_CATALOG

__all__ = [
    "DECISION_ROUTE_CHAT",
    "DECISION_ROUTE_QUEUE",
    "decision_route",
    "rejected_capability",
]


def rejected_capability(approval_type: str | None) -> str | None:
    """The capability a rejection is evidence about, or None when there isn't one.

    This was a bare `split(":", 1)[1]` applied to every approval type, so the
    trust ladder was fed whatever happened to follow a colon:

      * `step:email.send` -> "email.send". Correct, and the case the split was
        written for.
      * `tool:send_email` -> "send_email", a TOOL name. No capability catalogue
        contains it, so `record_approval_decision` created a TrustState row for
        a capability that does not exist, applied a rejection cooldown to it,
        and left it in the trust dashboard for ever.
      * `filter_proposal` -> no colon, so the whole literal. Declining a
        proposal to quiet some mailing lists demoted a capability named
        "filter_proposal".

    So the suffix is CHECKED rather than assumed: it counts only if the
    catalogue knows it. That also admits the Governor's plan-level approvals,
    whose type is a bare capability with no prefix at all (`email.send`), which
    a prefix-based rule would have had to special-case and did not.

    Returning None is not a silent skip — it is the honest answer that a
    rejected filter proposal says nothing about any capability, and recording it
    anyway would let an unrelated "no" demote real authority.
    """
    if not approval_type:
        return None
    candidate = approval_type.split(":", 1)[1] if ":" in approval_type else approval_type
    return candidate if candidate in CAPABILITY_CATALOG else None


# Where a decision on this approval has to be sent. The chat gate stamps
# `artifact_refs["chat"] = True` and the decide endpoints 409 those on purpose:
# a chat approval resumes a suspended turn through /v1/muldro/chat/resume, and
# nothing else can answer it.
#
# The CLIENT could not tell the difference — `artifact_refs` is on the detail
# response, not the list — so a general review queue offered Approve on a row
# the server would refuse, and the failure came back as an unexplained error.
# The server knows; it should say so rather than let the client guess.
DECISION_ROUTE_QUEUE = "queue"
DECISION_ROUTE_CHAT = "chat"


def decision_route(artifact_refs: dict | None) -> str:
    """Which surface can decide this approval."""
    refs = artifact_refs if isinstance(artifact_refs, dict) else {}
    return DECISION_ROUTE_CHAT if refs.get("chat") is True else DECISION_ROUTE_QUEUE
