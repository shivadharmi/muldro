"""Prepared-work review queue detail tab builder.

When a write needs a human and none is on the turn, both write gates record the call as
an ``Approval`` (``approval_type == "prepared_action"``, ``artifact_refs["prepared"] is
True``) and let the turn finish rather than blocking on nobody. Confirming one replays the
recorded payload exactly. This tab is the founder-facing surface for those rows — and the
ONLY place a prepared action can be acted on. Nothing else re-asks.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailTabResponse

from ._shared import _empty_tab, _format_ts, _section

logger = logging.getLogger(__name__)

QUEUE_LIMIT = 25
"""Rows rendered per fetch. The queue is a review surface, not an archive."""

_UNKNOWN_OUTCOME_MARKER = "still in flight"
"""Substring of the one ``prepared_error`` that is NOT retryable.

If a confirm is killed mid-execute the idempotency ledger row stays ``in_flight``, and every
later confirm returns "a prior attempt is still in flight — not re-fired" permanently — the
ledger reopens ``failed``, never ``in_flight``. That is the correct fail-closed choice (we do
not know whether the write fired, so we will not fire it again), but a row that soft-errors
forever behind an Approve button is a lie. Such rows are rendered as an unknown outcome to
be checked at the destination, with the approve control withheld.
"""


def _is_unknown_outcome(error: str | None) -> bool:
    return bool(error) and _UNKNOWN_OUTCOME_MARKER in error.lower()


def _row_children(idx: int, apr: Any) -> list[A2UIComponent]:
    refs = apr.artifact_refs if isinstance(apr.artifact_refs, dict) else {}
    aid = apr.approval_id
    children: list[A2UIComponent] = []

    capability = refs.get("capability") or refs.get("tool_name") or ""
    if capability:
        children.append(r.badge(f"pq_{idx}_cap", capability))

    children.append(r.text(f"pq_{idx}_summary", apr.summary or apr.title or "Prepared action"))

    # Already a JSON STRING (``redact_tool_input`` serialises before persisting), so ``str()``
    # is a no-op that documents the type rather than converting it.
    tool_input = refs.get("tool_input")
    if tool_input:
        children.append(r.code_block(f"pq_{idx}_input", str(tool_input), language="json"))
        if refs.get("tool_input_truncated"):
            children.append(
                r.caption(
                    f"pq_{idx}_clipped",
                    "Payload clipped for storage — showing the start. This action cannot be "
                    "replayed exactly as reviewed.",
                )
            )

    risk = apr.risk_level or "medium"
    risk_variant = "warning" if risk in ("high", "critical") else "default"
    children.append(r.badge(f"pq_{idx}_risk", f"Risk: {risk}", variant=risk_variant))
    children.append(r.caption(f"pq_{idx}_age", f"Prepared: {_format_ts(apr.created_at)}"))

    error = refs.get("prepared_error")
    unknown = _is_unknown_outcome(error)
    if unknown:
        children.append(
            r.alert(
                f"pq_{idx}_unknown",
                "A confirm was interrupted mid-execute, so whether this action reached its "
                "destination is UNKNOWN. It will not be re-sent. Check the destination to see "
                "what happened, then dismiss this row.",
                severity="warning",
                title="Outcome unknown — check the destination",
            )
        )
    elif error:
        children.append(
            r.alert(
                f"pq_{idx}_err",
                f"Not yet run: {error}. Confirming again will retry it.",
                severity="warning",
            )
        )

    actions: list[A2UIComponent] = []
    if not unknown:
        actions.append(
            r.button(
                f"pq_{idx}_approve",
                "Approve",
                variant="primary",
                action_payload={"type": "approval.approve", "approval_id": aid},
            )
        )
    actions.append(
        r.button(
            f"pq_{idx}_reject",
            "Dismiss" if unknown else "Reject",
            variant="secondary" if unknown else "danger",
            action_payload={"type": "approval.reject", "approval_id": aid},
        )
    )
    children.append(r.row(f"pq_{idx}_actions", actions))
    return children


async def build_prepared_work_queue(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Render every prepared action still awaiting the founder's decision.

    Scoped by the authenticated ``user_id`` rather than by a workspace parsed out of the
    surface id: the prepared-work surface carries no record reference, so the ephemeral
    tenant guard (``_verify_ephemeral_ownership``) has nothing to check. Doing the scoping
    here means a guessed or enumerated surface id returns the guesser's OWN queue rather
    than someone else's. Without a ``user_id`` the tab renders empty — it never guesses.
    """
    from src.deep_runtime.middleware.approval_persistence import PREPARED_APPROVAL_TYPE
    from src.models.approvals import Approval

    user_id = kwargs.get("user_id")
    if not user_id:
        logger.warning("prepared-work queue requested without a user_id — rendering empty")
        return _empty_tab("queue", "Nothing is waiting for your review.")

    result = await db.execute(
        select(Approval)
        .where(
            Approval.user_id == user_id,
            Approval.approval_type == PREPARED_APPROVAL_TYPE,
            Approval.status == "pending",
        )
        .order_by(Approval.created_at.desc())
        .limit(QUEUE_LIMIT)
    )
    rows = list(result.scalars().all())
    if not rows:
        return _empty_tab("queue", "Nothing is waiting for your review.")

    sections = []
    for idx, apr in enumerate(rows):
        capability = ""
        if isinstance(apr.artifact_refs, dict):
            capability = apr.artifact_refs.get("capability") or apr.artifact_refs.get(
                "tool_name", ""
            )
        title = capability or apr.title or f"Prepared action {idx + 1}"
        sections.append(
            # Only the first row opens. A long queue rendered fully expanded is a wall of
            # text, not a review surface.
            _section(f"pq_{idx}", title, _row_children(idx, apr), collapsed=idx > 0)
        )

    return DetailTabResponse(tab_id="queue", sections=sections)
