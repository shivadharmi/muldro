"""Per-workspace entitlement checks for the chat permission model (P2).

``bypass`` mode is broad write authority — the user opts fully out of action-time
confirmations for a turn — so it is granted ONLY when a workspace has explicitly opted
in via ``Workspace.settings["allow_bypass"]`` (JSONB; NO migration). Every failure mode
(unset/false flag, missing workspace, DB error) resolves to ``False`` (fail-safe): a
workspace that has not explicitly opted in NEVER receives ``bypass``, and the caller
downgrades it to ``auto``.
"""

from __future__ import annotations

import logging

from src.models.users import Workspace

logger = logging.getLogger(__name__)


async def workspace_allows_bypass(db_factory, workspace_id: str) -> bool:
    """True iff ``workspace_id`` has explicitly opted into ``bypass`` permission mode.

    Reads ``Workspace.settings["allow_bypass"]`` (a JSONB flag). Defaults to ``False``
    and FAILS SAFE — a missing workspace or any error returns ``False`` so ``bypass``
    is never granted implicitly.

    Args:
        db_factory: Async-context-manager session factory (``async with db_factory()``).
        workspace_id: The tenant to check.
    """
    try:
        async with db_factory() as db:
            workspace = await db.get(Workspace, workspace_id)
            if workspace is None:
                return False
            # STRICT: require the JSONB flag to be exactly boolean ``true``. Using ``bool(...)``
            # would treat a misconfigured string like ``"false"`` (Python-truthy) as opt-in —
            # a footgun for a broad-write entitlement. Only a real ``True`` grants bypass.
            return (workspace.settings or {}).get("allow_bypass") is True
    except Exception:
        logger.warning(
            "workspace_allows_bypass lookup failed for %s — defaulting to False (fail-safe)",
            workspace_id,
            exc_info=True,
        )
        return False
