"""Step 6C Task 2.1 (CF-3): real-DB proof of the deep-gate idempotency fence.

The deep-gate approval idempotency key is being promoted from ``Approval.artifact_refs``
JSONB (thread_id, tool_call_id) to nullable ``thread_id``/``tool_call_id`` columns fenced
by a PARTIAL UNIQUE index on ``(workspace_id, thread_id, tool_call_id)`` WHERE both are
non-null. This suite proves the DB constraint has teeth:

  1. two rows with the SAME (workspace_id, thread_id, tool_call_id) tuple are rejected;
  2. rows with NULL tuples (legacy/autonomous approvals) never conflict (partial index);
  3. the SAME tuple in a DIFFERENT workspace does not conflict (workspace is in the key).

These tests do NOT depend on Task 2.2's ``create_approval`` column-writing — they set the
``thread_id``/``tool_call_id`` columns directly on the ORM object after ``create_approval``.

No Docker/Anthropic dependency: skips (does not fail) when Postgres is unreachable,
mirroring ``tests/test_deep_gate_end_to_end.py``. Each test builds its own engine bound to
its own event loop (this repo's custom async-test hook runs every test via a fresh
``asyncio.run``) and disposes it in a ``finally``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.deep_runtime.middleware.trust_gate import _decide_and_maybe_persist
from src.models.approvals import Approval
from src.models.users import User, Workspace
from src.services.approval_service import create_approval
from src.services.risk_assessor import RiskAssessment

TRUST_GATE_MODULE = "src.deep_runtime.middleware.trust_gate"


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip.

    Mirrors ``tests/test_deep_gate_end_to_end.py``: a raw asyncpg connect on its own
    throwaway loop, never touching the app's process-wide cached engine.
    """
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover - environment-dependent
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _env():
    """Yield ``(factory, user_id, workspace_id, workspace_id_2)`` with FK parents seeded.

    Seeds one User and TWO Workspaces (the second is for the workspace-scoping control).
    Teardown deletes Approvals for both workspaces, then the Workspaces + User, then
    disposes the engine — all on this test's own loop.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    workspace_id_2 = f"ws2_{suffix}"
    try:
        async with factory() as db:
            db.add(
                User(
                    user_id=user_id,
                    email=f"idem-{suffix}@example.com",
                    display_name="idempotency-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="idem-ws", owner_user_id=user_id))
            db.add(Workspace(workspace_id=workspace_id_2, name="idem-ws2", owner_user_id=user_id))
            await db.commit()
        yield factory, user_id, workspace_id, workspace_id_2
    finally:
        try:
            async with factory() as db:
                await db.execute(
                    delete(Approval).where(
                        Approval.workspace_id.in_([workspace_id, workspace_id_2])
                    )
                )
                await db.execute(
                    delete(Workspace).where(
                        Workspace.workspace_id.in_([workspace_id, workspace_id_2])
                    )
                )
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


def _tool_refs(*, thread_id: str, tool_call_id: str) -> dict:
    return {
        "tool_name": "send_email",
        "thread_id": thread_id,
        "tool_call_id": tool_call_id,
        "capability": "email.send",
    }


async def test_duplicate_thread_tool_call_is_rejected_by_unique_index():
    """Two Approvals with the SAME (workspace_id, thread_id, tool_call_id) tuple must
    violate the partial UNIQUE index — the DB fence against concurrent replays."""
    async with _env() as (factory, user_id, workspace_id, _ws2):
        thread_id = "chat_abc"
        tool_call_id = "tc_1"

        async with factory() as db:
            appr = await create_approval(
                db,
                user_id=user_id,
                workspace_id=workspace_id,
                approval_type="tool:send_email",
                title="Send email",
                requested_by=user_id,
                artifact_refs=_tool_refs(thread_id=thread_id, tool_call_id=tool_call_id),
            )
            # Task 2.2 will do this inside create_approval; here we set the columns directly.
            appr.thread_id = thread_id
            appr.tool_call_id = tool_call_id
            await db.commit()

        # SECOND fresh session: same tuple → the fence must reject the duplicate.
        async with factory() as db:
            dup = await create_approval(
                db,
                user_id=user_id,
                workspace_id=workspace_id,
                approval_type="tool:send_email",
                title="Send email (duplicate replay)",
                requested_by=user_id,
                artifact_refs=_tool_refs(thread_id=thread_id, tool_call_id=tool_call_id),
            )
            dup.thread_id = thread_id
            dup.tool_call_id = tool_call_id
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()


async def test_null_tuple_rows_do_not_conflict():
    """NEGATIVE control: two Approvals with NULL thread_id/tool_call_id columns (legacy /
    autonomous approvals) must BOTH commit — the partial index excludes NULL-tuple rows."""
    async with _env() as (factory, user_id, workspace_id, _ws2):
        async with factory() as db:
            await create_approval(
                db,
                user_id=user_id,
                workspace_id=workspace_id,
                approval_type="tool:send_email",
                title="Legacy approval A",
                requested_by=user_id,
                artifact_refs={"tool_name": "send_email"},
            )
            await db.commit()

        async with factory() as db:
            await create_approval(
                db,
                user_id=user_id,
                workspace_id=workspace_id,
                approval_type="tool:send_email",
                title="Legacy approval B",
                requested_by=user_id,
                artifact_refs={"tool_name": "send_email"},
            )
            # No IntegrityError expected — NULL tuples are excluded from the partial index.
            await db.commit()

        async with factory() as db:
            from sqlalchemy import func, select

            count = (
                await db.execute(
                    select(func.count(Approval.approval_id)).where(
                        Approval.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
            assert count == 2, f"both NULL-tuple approvals must persist; got {count}"


async def test_same_tuple_different_workspace_does_not_conflict():
    """SCOPING control: the SAME (thread_id, tool_call_id) in a DIFFERENT workspace must
    NOT conflict — workspace_id is part of the unique key."""
    async with _env() as (factory, user_id, workspace_id, workspace_id_2):
        thread_id = "chat_shared"
        tool_call_id = "tc_shared"

        async with factory() as db:
            a = await create_approval(
                db,
                user_id=user_id,
                workspace_id=workspace_id,
                approval_type="tool:send_email",
                title="ws1 approval",
                requested_by=user_id,
                artifact_refs=_tool_refs(thread_id=thread_id, tool_call_id=tool_call_id),
            )
            a.thread_id = thread_id
            a.tool_call_id = tool_call_id
            await db.commit()

        async with factory() as db:
            b = await create_approval(
                db,
                user_id=user_id,
                workspace_id=workspace_id_2,
                approval_type="tool:send_email",
                title="ws2 approval (same tuple, different workspace)",
                requested_by=user_id,
                artifact_refs=_tool_refs(thread_id=thread_id, tool_call_id=tool_call_id),
            )
            b.thread_id = thread_id
            b.tool_call_id = tool_call_id
            # Different workspace_id → no conflict.
            await db.commit()


async def test_decide_and_persist_writes_columns_and_get_path_returns_same_row():
    """Task 2.2: ``_decide_and_maybe_persist`` creates an Approval via ``create_approval``
    that POPULATES the promoted ``thread_id``/``tool_call_id`` columns (Step 1), and a
    replay with the SAME (workspace_id, thread_id, tool_call_id) tuple finds the row BY
    THOSE COLUMNS and reuses its id — exactly ONE row, no duplicate (Step 2 get-path).

    ``TrustEngine`` is patched so ``evaluate`` returns ``approval_required`` (deterministic
    create path); ``create_approval`` runs FOR REAL so the columns are actually written.
    """
    async with _env() as (factory, user_id, workspace_id, _ws2):
        thread_id = "chat_decide_1"
        tool_call_id = "tc_decide_1"
        risk = RiskAssessment(
            risk_level="high",
            reasoning="sends external email",
            reversible=False,
            blast_radius="external_single",
        )
        fake_te = MagicMock()
        fake_te.evaluate = AsyncMock(
            return_value=SimpleNamespace(decision="approval_required", justification="risky")
        )

        # Phase 1 — CREATE path: real create_approval runs and writes the columns.
        with patch(f"{TRUST_GATE_MODULE}.TrustEngine", return_value=fake_te):
            require_approval, approval_id = await _decide_and_maybe_persist(
                name="send_email",
                capability="email.send",
                risk=risk,
                workspace_id=workspace_id,
                user_id=user_id,
                thread_id=thread_id,
                tool_call_id=tool_call_id,
                agent_name="executor",
                db_factory=factory,
            )
        assert require_approval is True
        assert approval_id and approval_id.startswith("apr_")

        # Exactly ONE row for the tuple, and the COLUMNS are populated (proves Step 1).
        async with factory() as db:
            rows = (
                (
                    await db.execute(
                        select(Approval).where(
                            Approval.workspace_id == workspace_id,
                            Approval.thread_id == thread_id,
                            Approval.tool_call_id == tool_call_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].approval_id == approval_id
        assert rows[0].thread_id == thread_id
        assert rows[0].tool_call_id == tool_call_id

        # Phase 2 — GET path: the SAME tuple resolves to the committed row, no duplicate.
        with patch(f"{TRUST_GATE_MODULE}.TrustEngine", return_value=fake_te):
            require_approval_2, approval_id_2 = await _decide_and_maybe_persist(
                name="send_email",
                capability="email.send",
                risk=risk,
                workspace_id=workspace_id,
                user_id=user_id,
                thread_id=thread_id,
                tool_call_id=tool_call_id,
                agent_name="executor",
                db_factory=factory,
            )
        assert require_approval_2 is True
        assert approval_id_2 == approval_id  # same id → the get path, not a second create

        async with factory() as db:
            count = (
                await db.execute(
                    select(func.count(Approval.approval_id)).where(
                        Approval.workspace_id == workspace_id,
                        Approval.thread_id == thread_id,
                        Approval.tool_call_id == tool_call_id,
                    )
                )
            ).scalar_one()
        assert count == 1, f"get path must not duplicate; got {count} rows"
