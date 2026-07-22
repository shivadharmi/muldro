"""Real-DB integration proof that the (workspace_id, identity_key) UNIQUE index
enforces exactly-once. The mocked tests fake the IntegrityError; this one makes
Postgres actually raise it, so the constraint itself is under test.

Skips (does not fail) when Postgres is unreachable, so the suite stays green in
no-DB environments.

Each test builds its OWN engine bound to its OWN event loop (this repo's conftest
runs every async test via a fresh ``asyncio.run``) and disposes it in-loop. The
process-wide cached engine from ``src.models.database`` pools connections against
the first loop that touched it, so reusing it across the per-test loops raises
"attached to a different loop" / "Event loop is closed" on teardown. A dedicated
per-test engine avoids that entirely.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.idempotency_ledger import IdempotencyLedgerEntry
from src.models.users import User, Workspace
from src.services.idempotency.ledger import IdempotencyLedger


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip.

    Deliberately does NOT touch the app's cached engine — that would build the
    process-wide SQLAlchemy engine on this throwaway loop. A raw asyncpg connect
    on its own loop leaves the app engine untouched."""
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
async def _ledger_env():
    """Yields ``(sessionmaker, workspace_id)`` with the FK parents (User owner +
    Workspace) already seeded. On exit: delete the Workspace (CASCADE removes the
    ledger rows) and the User, then dispose the engine — all in this loop."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(
                User(
                    user_id=user_id,
                    email=f"ledger-test-{suffix}@example.com",
                    display_name="ledger-test",
                )
            )
            db.add(
                Workspace(
                    workspace_id=workspace_id,
                    name="ledger-test-ws",
                    owner_user_id=user_id,
                )
            )
            await db.commit()
        yield factory, workspace_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


async def _row_status(factory, ledger_id: str) -> str | None:
    async with factory() as db:
        row = await db.get(IdempotencyLedgerEntry, ledger_id)
        return None if row is None else row.status


async def test_real_unique_constraint_enforces_exactly_once():
    """Test A: fresh reserve inserts in_flight; record_success completes it; a
    second reserve of the SAME identity hits the real UNIQUE index -> the service
    resolves it to already_done with the stored result."""
    async with _ledger_env() as (factory, workspace_id):
        ledger = IdempotencyLedger(factory)
        identity_key = f"{workspace_id}:st1:email.send:sem:{ULID()}"

        first = await ledger.reserve(
            workspace_id=workspace_id,
            run_id="run_A",
            step_id="st1",
            capability="email.send",
            identity_key=identity_key,
        )
        assert first.already_done is False
        assert first.in_flight_conflict is False
        assert first.ledger_id is not None
        assert await _row_status(factory, first.ledger_id) == "in_flight"

        await ledger.record_success(first.ledger_id, {"status": "sent"})
        assert await _row_status(factory, first.ledger_id) == "completed"

        # Second reserve, SAME (workspace_id, identity_key) -> real IntegrityError.
        second = await ledger.reserve(
            workspace_id=workspace_id,
            run_id="run_A",
            step_id="st1",
            capability="email.send",
            identity_key=identity_key,
        )
        assert second.already_done is True
        assert second.result == {"status": "sent"}
        assert second.ledger_id == first.ledger_id


async def test_real_failed_entry_is_reopened_for_retry():
    """Test B: a failed prior attempt (effect did not land) -> the next reserve
    reopens the reservation for retry (not already_done, not a conflict)."""
    async with _ledger_env() as (factory, workspace_id):
        ledger = IdempotencyLedger(factory)
        identity_key = f"{workspace_id}:st1:email.send:sem:{ULID()}"

        first = await ledger.reserve(
            workspace_id=workspace_id,
            run_id="run_B",
            step_id="st1",
            capability="email.send",
            identity_key=identity_key,
        )
        assert first.already_done is False

        await ledger.mark_failed(first.ledger_id)
        assert await _row_status(factory, first.ledger_id) == "failed"

        second = await ledger.reserve(
            workspace_id=workspace_id,
            run_id="run_B",
            step_id="st1",
            capability="email.send",
            identity_key=identity_key,
        )
        assert second.already_done is False
        assert second.in_flight_conflict is False
        assert second.ledger_id == first.ledger_id
        assert await _row_status(factory, first.ledger_id) == "in_flight"


async def test_real_in_flight_entry_reports_conflict():
    """Test C: an in_flight prior attempt (possibly killed mid-call) -> the next
    reserve reports in_flight_conflict (fail-closed against a double-fire)."""
    async with _ledger_env() as (factory, workspace_id):
        ledger = IdempotencyLedger(factory)
        identity_key = f"{workspace_id}:st1:email.send:sem:{ULID()}"

        first = await ledger.reserve(
            workspace_id=workspace_id,
            run_id="run_C",
            step_id="st1",
            capability="email.send",
            identity_key=identity_key,
        )
        assert first.already_done is False
        assert await _row_status(factory, first.ledger_id) == "in_flight"

        second = await ledger.reserve(
            workspace_id=workspace_id,
            run_id="run_C",
            step_id="st1",
            capability="email.send",
            identity_key=identity_key,
        )
        assert second.in_flight_conflict is True
        assert second.already_done is False
        assert second.ledger_id == first.ledger_id
