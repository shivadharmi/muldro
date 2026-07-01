"""The idempotency ledger service: reserve-before-write, record-on-success.

The (workspace_id, identity_key) UNIQUE index is the authoritative gate; the
inline lookup + IntegrityError-on-commit fallback mirrors event_processor's
concurrent-dedup pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from src.models.idempotency_ledger import IdempotencyLedgerEntry

logger = logging.getLogger(__name__)


def _ledger_lookup_stmt(workspace_id: str, identity_key: str) -> Select:
    """Workspace-scoped lookup of an existing ledger entry. Extracted so the
    isolation test can compile it (no DB)."""
    return select(IdempotencyLedgerEntry).where(
        IdempotencyLedgerEntry.workspace_id == workspace_id,
        IdempotencyLedgerEntry.identity_key == identity_key,
    )


@dataclass(frozen=True, slots=True)
class ReserveOutcome:
    already_done: bool  # a completed entry exists -> skip the call, use result
    in_flight_conflict: bool  # an in_flight entry exists (killed mid-call) -> do NOT re-fire
    result: dict | None  # stored result when already_done
    identity_key: str
    ledger_id: str | None  # the reserved row to record_success/mark_failed against


class IdempotencyLedger:
    def __init__(self, db_factory):
        self._db_factory = db_factory

    async def reserve(
        self,
        *,
        workspace_id: str,
        run_id: str | None,
        step_id: str | None,
        capability: str,
        identity_key: str,
        provider_token: str | None = None,
    ) -> ReserveOutcome:
        """Reserve idempotency for a write, capturing the identity at first attempt.

        CALLER CONTRACT: check the outcome before firing the external effect.
          * already_done=True      -> DO NOT fire; use `result` (the first
                                        attempt already completed).
          * in_flight_conflict=True -> DO NOT fire; a prior attempt is in-flight
                                        (killed mid-call). Fail-closed until a
                                        read-back exists (a later step).
          * otherwise               -> fire the effect, then record_success()
                                        (or mark_failed()) against `ledger_id`.
        """
        ledger_id = f"idem_{ULID()}"
        async with self._db_factory() as db:
            entry = IdempotencyLedgerEntry(
                ledger_id=ledger_id,
                workspace_id=workspace_id,
                run_id=run_id,
                step_id=step_id,
                capability=capability,
                identity_key=identity_key,
                status="in_flight",
                provider_token=provider_token,
            )
            db.add(entry)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return await self._resolve_existing(db, workspace_id, identity_key)
            return ReserveOutcome(
                already_done=False,
                in_flight_conflict=False,
                result=None,
                identity_key=identity_key,
                ledger_id=ledger_id,
            )

    async def _resolve_existing(self, db, workspace_id: str, identity_key: str) -> ReserveOutcome:
        result = await db.execute(_ledger_lookup_stmt(workspace_id, identity_key))
        row = result.scalar_one_or_none()
        if row is None:
            # Lost/rolled-back row (rare) — proceed as a fresh reservation.
            logger.warning("[idempotency] conflict with no row for key=%s", identity_key)
            return ReserveOutcome(
                already_done=False,
                in_flight_conflict=False,
                result=None,
                identity_key=identity_key,
                ledger_id=None,
            )
        if row.status == "completed":
            return ReserveOutcome(
                already_done=True,
                in_flight_conflict=False,
                result=row.result_json,
                identity_key=identity_key,
                ledger_id=row.ledger_id,
            )
        if row.status == "failed":
            # Reopen for retry, but guard against a concurrent reopen: only the
            # caller whose UPDATE actually flips failed->in_flight may proceed. A
            # racing caller sees rowcount==0 (the row is no longer 'failed') and
            # is treated as an in_flight conflict (fail-closed, no double-fire).
            reopened = await db.execute(
                update(IdempotencyLedgerEntry)
                .where(
                    IdempotencyLedgerEntry.ledger_id == row.ledger_id,
                    IdempotencyLedgerEntry.status == "failed",
                )
                .values(status="in_flight", result_json=None)
            )
            await db.commit()
            if reopened.rowcount == 0:
                return ReserveOutcome(
                    already_done=False,
                    in_flight_conflict=True,
                    result=None,
                    identity_key=identity_key,
                    ledger_id=row.ledger_id,
                )
            return ReserveOutcome(
                already_done=False,
                in_flight_conflict=False,
                result=None,
                identity_key=identity_key,
                ledger_id=row.ledger_id,
            )
        # in_flight: killed mid-call -> fail-closed against a double-fire.
        return ReserveOutcome(
            already_done=False,
            in_flight_conflict=True,
            result=None,
            identity_key=identity_key,
            ledger_id=row.ledger_id,
        )

    async def record_success(self, ledger_id: str, result: dict | None) -> None:
        async with self._db_factory() as db:
            row = await db.get(IdempotencyLedgerEntry, ledger_id)
            if row is None:
                logger.warning("[idempotency] record_success: no row %s", ledger_id)
                return
            row.status = "completed"
            row.result_json = result
            row.completed_at = datetime.now(timezone.utc)
            await db.commit()

    async def mark_failed(self, ledger_id: str) -> None:
        async with self._db_factory() as db:
            row = await db.get(IdempotencyLedgerEntry, ledger_id)
            if row is None:
                logger.warning("[idempotency] mark_failed: no row %s", ledger_id)
                return
            row.status = "failed"
            await db.commit()
