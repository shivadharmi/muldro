"""RunDetailStore — the single owner of the run-detail facts extracted off the hot
TaskRun row (Step 5 §4.8, one-owner-per-fact): policy_decision (durable) and
context_pack (heavy, ephemeral, TTL'd with a dereference/expiry render fallback).

Both fields live on the 1:1 ``task_run_details`` row keyed by run_id. Writes upsert
(a run may write policy_decision at creation and context_pack right after, or refresh
the pack later). ``get_context_pack`` applies the expiry fallback: an absent row, a
NULLed pack, or an expired pack all dereference to None so every reader's ``... or {}``
renders gracefully.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.task_graph import TaskRunDetail

CONTEXT_PACK_TTL_DAYS = 30


class RunDetailStore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def upsert_context_pack(
        self, run_id: str, workspace_id: str, pack: dict, ttl_days: int = CONTEXT_PACK_TTL_DAYS
    ) -> None:
        """Store the context pack by-ref with a TTL (resets expiry on each write)."""
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
        stmt = (
            pg_insert(TaskRunDetail)
            .values(
                run_id=run_id,
                workspace_id=workspace_id,
                context_pack=pack,
                context_pack_expires_at=expires_at,
            )
            .on_conflict_do_update(
                index_elements=["run_id"],
                set_={"context_pack": pack, "context_pack_expires_at": expires_at},
            )
        )
        await self._db.execute(stmt)

    async def upsert_policy_decision(self, run_id: str, workspace_id: str, decision: dict) -> None:
        """Store the durable policy decision (no TTL)."""
        stmt = (
            pg_insert(TaskRunDetail)
            .values(run_id=run_id, workspace_id=workspace_id, policy_decision=decision)
            .on_conflict_do_update(index_elements=["run_id"], set_={"policy_decision": decision})
        )
        await self._db.execute(stmt)

    async def get_context_pack(self, run_id: str) -> dict | None:
        """Dereference the context pack; None if absent, NULLed, or expired (fallback)."""
        row = (
            await self._db.execute(select(TaskRunDetail).where(TaskRunDetail.run_id == run_id))
        ).scalar_one_or_none()
        if row is None or row.context_pack is None:
            return None
        if row.context_pack_expires_at is not None:
            expires = row.context_pack_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                return None
        return row.context_pack

    async def get_policy_decision(self, run_id: str) -> dict | None:
        row = (
            await self._db.execute(select(TaskRunDetail).where(TaskRunDetail.run_id == run_id))
        ).scalar_one_or_none()
        return row.policy_decision if row is not None else None
