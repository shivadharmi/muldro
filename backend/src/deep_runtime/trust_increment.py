"""Deep-runtime trust-increment on a CONFIRMED gated write (Step 7C P2).

Mirrors the autonomous deferred tick (deferred_verification_tick.py:80-107): the DB-only
record_approval_decision wrapped in a begin_nested() SAVEPOINT — the 6C #4 / 7A-P0
session-poisoning discipline, from the START — inside a best-effort try/except (a trust write
must NEVER crash a turn).

decision_type="approved": the deep interrupt verdict is a bare "approve"; the modified/approved
distinction is not captured on the deep gate today (a Step-10 refinement).

NOTE on the SAVEPOINT: this helper opens a FRESH, dedicated session per increment, so the
begin_nested() is DEFENSIVE here (a poisoned flush aborts only this throwaway session, which the
`async with db_factory()` discards regardless). It is kept for sibling-consistency with
record_auto_execution_outcome / the deferred tick and to stay correct if a shared session is ever
passed. The genuinely load-bearing guard on this path is the best-effort try/except.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


async def record_deep_confirmed_outcome(
    *, db_factory: Callable[[], Any], workspace_id: str, capability: str, risk_level: str
) -> None:
    """Increment trust for a CONFIRMED gated deep write. Best-effort: never raises."""
    from src.services.risk_assessor import record_approval_decision

    try:
        async with db_factory() as db:
            async with db.begin_nested():
                await record_approval_decision(db, workspace_id, capability, risk_level, "approved")
            await db.commit()
    except Exception:
        logger.debug("[deep_runtime] deep trust-increment best-effort failed", exc_info=True)
