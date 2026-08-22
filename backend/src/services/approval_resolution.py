"""Shared classification of a rich ``ApprovalContext`` carried on a payload.

The rich context used to be read back from a persisted copy of the last surface
update. Nothing persists a view any more — a view is a pure function of a live
row, so there is no view to store — which leaves the rich context with no
source: every caller now passes ``None`` and takes the ABSENT branch, i.e. the
thin context assembled from the ``Approval`` row itself.

The classification is kept rather than inlined because it is the SINGLE place
the absent / well-formed / malformed rule is stated, and it still applies to
whatever a caller passes. Each caller owns its own action on ABSENT/MALFORMED.
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import ValidationError

from src.contracts import ApprovalContext

logger = logging.getLogger(__name__)


class PersistedApprovalStatus(str, Enum):
    """Classification of the approval persisted on a surface payload."""

    ABSENT = "absent"  # no persisted approval → caller uses its own fallback
    RICH = "rich"  # well-formed rich ApprovalContext
    MALFORMED = "malformed"  # present but malformed → caller must FAIL CLOSED


def extract_persisted_rich_approval(
    surface_payload: dict | None,
) -> tuple[PersistedApprovalStatus, ApprovalContext | None]:
    """Classify ``surface_payload["last_surface_update"]["approval"]``.

    Returns:
      * ``(ABSENT, None)`` — no persisted approval (caller falls back, byte-neutral);
      * ``(RICH, ctx)`` — a well-formed ``ApprovalContext``;
      * ``(MALFORMED, None)`` — present but not a dict / missing a required field
        (caller must FAIL CLOSED — never half-render).
    """
    approval_raw: object = None
    if isinstance(surface_payload, dict):
        last = surface_payload.get("last_surface_update")
        if isinstance(last, dict):
            approval_raw = last.get("approval")

    if approval_raw is None:
        return (PersistedApprovalStatus.ABSENT, None)
    if not isinstance(approval_raw, dict):
        logger.warning("persisted approval is not a dict — malformed, failing closed")
        return (PersistedApprovalStatus.MALFORMED, None)
    try:
        return (PersistedApprovalStatus.RICH, ApprovalContext.model_validate(approval_raw))
    except ValidationError:
        logger.warning("persisted approval context malformed — failing closed")
        return (PersistedApprovalStatus.MALFORMED, None)
