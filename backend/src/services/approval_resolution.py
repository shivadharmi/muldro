"""Shared resolution of a persisted rich ``ApprovalContext`` (B12 / P3.2).

The autonomous surface machine persists the full ``ApprovalContext`` under
``UISurface.payload["last_surface_update"]["approval"]`` (execution_surface_emitter).
Every persisted approval surface — the history LIST endpoint and the run/summary
approval DETAIL tab — reads it from the SAME place so they all render the rich
context the live-WS path emits.

This module is the SINGLE source of the absent / well-formed / malformed rule.
Each caller applies its own fallback on ABSENT/MALFORMED (the classification is
shared; the action on each outcome is caller-specific).
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
