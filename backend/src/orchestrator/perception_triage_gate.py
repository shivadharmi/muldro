"""Triage-actionability gate for the perception Opus fast-path (Task 11).

Free functions, not a class: this is a pure "check triaged rows in the DB,
return a bool" concern with no state of its own. Folding it directly into
``PerceptionRunner`` would have pushed that file over the 800-line Python
cap (docs/engineering-standards.md §1), so it lives here instead.

Both functions read the ``importance_signals.actionable`` field the triage
service (``src.services.triage``) persists on ``NormalizedEvent`` rows, and
are used to skip the Opus Planner call entirely when a poll (or a batch of
cross-source polls) produced nothing but noise.
"""

from sqlalchemy import select

from src.models.events import NormalizedEvent
from src.services.event_processor import make_idempotency_key


async def has_actionable_events(db_factory, raw_events: list, workspace_id: str) -> bool:
    """True if any just-ingested event was triaged actionable.

    Looks up the stored ``NormalizedEvent`` rows for this poll's events (by
    idempotency key) and checks ``importance_signals.actionable``. Used to
    gate the per-source perception cycle's Planner call.
    """
    keys = [make_idempotency_key(r) for r in raw_events]
    if not keys:
        return False
    async with db_factory() as db:
        rows = (
            await db.execute(
                select(NormalizedEvent.importance_signals).where(
                    NormalizedEvent.workspace_id == workspace_id,
                    NormalizedEvent.idempotency_key.in_(keys),
                )
            )
        ).all()
    return any((r[0] or {}).get("actionable") for r in rows)


async def has_actionable_for_sources(
    db_factory, source_names: list[str], workspace_id: str
) -> bool:
    """True if any recently-ingested event from these sources was triaged
    actionable — the cross-source synthesis gate.

    Unlike ``has_actionable_events``, synthesis has no ``raw_events`` in hand
    (the scheduler only passes source names + counts), so this looks up the
    most recently ingested rows for these sources/workspace instead of
    matching specific idempotency keys.
    """
    if not source_names:
        return False
    async with db_factory() as db:
        rows = (
            await db.execute(
                select(NormalizedEvent.importance_signals)
                .where(
                    NormalizedEvent.workspace_id == workspace_id,
                    NormalizedEvent.source.in_(source_names),
                )
                .order_by(NormalizedEvent.ingested_at.desc())
                .limit(50)
            )
        ).all()
    return any((r[0] or {}).get("actionable") for r in rows)
