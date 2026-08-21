"""Real-DB regression: the Postgres UNIQUE index enforces exactly-once for prepared actions.

``tests/test_prepared_actions.py::test_a_double_confirm_fires_exactly_once`` already proves
``execute_prepared_action`` CALLS the ledger correctly, against an in-memory fake ledger. It
does NOT prove the exactly-once property holds, because the real gate is a Postgres UNIQUE
index on ``(workspace_id, identity_key)`` — ``ix_idempotency_ledger_ws_key`` in
``src/models/idempotency_ledger.py``. A fake that returns the right answers cannot demonstrate
that the database enforces anything; only a real ``IntegrityError`` under a real UNIQUE index
can. This file is that proof.

Patterned on ``tests/idempotency/test_ledger_db.py`` (same ledger, same real-DB conventions):
own engine/loop via ``NullPool``, a fresh ``User``/``Workspace`` FK-parent pair per test (a
``IdempotencyLedgerEntry.workspace_id`` FK with ``ondelete="CASCADE"`` requires the workspace
row to exist), and a ``_db_reachable`` skip guard so the suite stays green with Postgres down.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.users import User, Workspace
from src.services.idempotency.ledger import IdempotencyLedger
from src.services.prepared_actions import execute_prepared_action
from tests.test_prepared_actions import _approval, _registry_returning


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip.

    Deliberately does NOT touch the app's cached engine — that would build the
    process-wide SQLAlchemy engine on this throwaway loop. A raw asyncpg connect
    on its own loop leaves the app engine untouched.
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
async def _prepared_env():
    """Yields ``(sessionmaker, user_id, workspace_id)`` with the FK parents seeded.

    On exit: delete the Workspace (CASCADE removes the ledger rows) and the User, then
    dispose the engine — all on this test's own loop, mirroring ``_ledger_env``.
    """
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
                    email=f"prepared-realdb-{suffix}@example.com",
                    display_name="prepared-realdb-test",
                )
            )
            db.add(
                Workspace(
                    workspace_id=workspace_id,
                    name="prepared-realdb-ws",
                    owner_user_id=user_id,
                )
            )
            await db.commit()
        yield factory, user_id, workspace_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


async def test_prepared_action_double_confirm_executes_once():
    """Invariant 5 against the real UNIQUE index, not a fake ledger.

    The unit-test sibling proves ``execute_prepared_action`` CALLS the ledger correctly. Only
    this proves the property holds, because the actual gate is Postgres's
    (workspace_id, identity_key) UNIQUE index — a fake that returns the right answers cannot
    demonstrate that the database enforces anything.
    """
    async with _prepared_env() as (factory, user_id, workspace_id):
        approval = _approval(approval_id=f"apr_{ULID()}")
        # `_approval()` hardcodes its module's fixed WORKSPACE_ID/USER_ID constants; point
        # this row at the User/Workspace actually seeded above instead of editing the shared
        # helper (its FK-parent rows don't exist in this test's own transaction).
        approval.user_id = user_id
        approval.workspace_id = workspace_id

        tool = SimpleNamespace(name="send_email", capability="email.send")
        ledger = IdempotencyLedger(factory)
        fired = []

        async def _execute(name, args, uid, ws):
            fired.append(name)
            return {"status": "ok", "id": "msg_1"}

        with patch("src.services.prepared_actions.ToolRegistry", _registry_returning(tool)):
            first = await execute_prepared_action(
                approval, execute_tool=_execute, db_factory=factory, redis=None, ledger=ledger
            )
            second = await execute_prepared_action(
                approval, execute_tool=_execute, db_factory=factory, redis=None, ledger=ledger
            )

        assert first.executed is True
        assert second.executed is True, "a second confirm must report success, not an error"
        assert fired == ["send_email"], "the external write must fire exactly once"
