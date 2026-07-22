"""Post-action reconciliation (spec §4.5): CONFIRMED raises a belief, CONTRADICTED
lowers it; no resolvable entity / UNVERIFIED -> no-op. Confidence never touches the
gate (D6). Real Postgres."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity
from src.models.users import User, Workspace
from src.services.entity_facts.store import EntityFactStore
from src.services.verification.readback import VerifyVerdict


def _db_reachable() -> bool:
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
    except Exception:  # pragma: no cover
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _env_with_fact():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws = f"ws_{suffix}"
    eid = f"ent_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=uid, email=f"s4-{suffix}@example.com", display_name="s4"))
            db.add(Workspace(workspace_id=ws, name="s4-ws", owner_user_id=uid))
            await db.flush()
            db.add(
                Entity(
                    entity_id=eid,
                    user_id=uid,
                    workspace_id=ws,
                    entity_type="person",
                    canonical_name="Frank",
                )
            )
            await db.flush()
            fid, _ = await EntityFactStore(db).record_fact(
                entity_id=eid,
                workspace_id=ws,
                user_id=uid,
                attr_key="role",
                attr_value="CTO",
                origin="perception",
            )
            await db.commit()
        yield factory, ws, uid, eid, fid
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == ws))
                await db.execute(delete(User).where(User.user_id == uid))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_confirmed_raises_belief():
    from src.services.entity_facts.reconciliation import reconcile_verdict

    async with _env_with_fact() as (factory, ws, uid, eid, fid):
        async with factory() as db:
            before = (await EntityFactStore(db).get_fact(fid)).confidence
            await reconcile_verdict(
                db,
                workspace_id=ws,
                user_id=uid,
                verdict=VerifyVerdict.CONFIRMED,
                write_input={"entity_id": eid},
                write_output={},
            )
            await db.commit()
            after = (await EntityFactStore(db).get_fact(fid)).confidence
            assert after > before


async def test_contradicted_lowers_belief():
    from src.services.entity_facts.reconciliation import reconcile_verdict

    async with _env_with_fact() as (factory, ws, uid, eid, fid):
        async with factory() as db:
            before = (await EntityFactStore(db).get_fact(fid)).confidence
            await reconcile_verdict(
                db,
                workspace_id=ws,
                user_id=uid,
                verdict=VerifyVerdict.CONTRADICTED,
                write_input={"entity_id": eid},
                write_output={},
            )
            await db.commit()
            after = (await EntityFactStore(db).get_fact(fid)).confidence
            assert after < before


async def test_no_resolvable_entity_is_a_noop():
    from src.services.entity_facts.reconciliation import reconcile_verdict

    async with _env_with_fact() as (factory, ws, uid, eid, fid):
        async with factory() as db:
            await reconcile_verdict(
                db,
                workspace_id=ws,
                user_id=uid,
                verdict=VerifyVerdict.CONFIRMED,
                write_input={"to": "someone@example.com"},
                write_output={},
            )
            await db.commit()  # must not raise


async def test_unverified_verdict_is_a_noop():
    from src.services.entity_facts.reconciliation import reconcile_verdict

    async with _env_with_fact() as (factory, ws, uid, eid, fid):
        async with factory() as db:
            before = (await EntityFactStore(db).get_fact(fid)).confidence
            await reconcile_verdict(
                db,
                workspace_id=ws,
                user_id=uid,
                verdict=VerifyVerdict.UNVERIFIED,
                write_input={"entity_id": eid},
                write_output={},
            )
            await db.commit()
            assert (await EntityFactStore(db).get_fact(fid)).confidence == before


async def test_reconcile_failure_does_not_poison_the_outer_session():
    """A failed belief write must roll back only its SAVEPOINT and leave the shared
    session usable for its own commit (no PendingRollbackError). Regression for the
    swallowed-flush-failure bug (Step 4 Task 7 review)."""
    from unittest.mock import patch

    from sqlalchemy import text

    from src.services.entity_facts import store as store_mod
    from src.services.entity_facts.reconciliation import reconcile_verdict

    async with _env_with_fact() as (factory, ws, uid, eid, fid):
        async with factory() as db:
            # Force the mutating write to blow up mid-reconcile the way a failed
            # flush() does: a DB-level error leaves the session in a failed-transaction
            # state. Without the surrounding SAVEPOINT that state escapes and the outer
            # commit below raises PendingRollbackError.
            async def _boom(self, fact_id):
                await self._db.execute(text("SELECT this_column_does_not_exist"))

            # Open the outer transaction first (as a live primary op would have), so a
            # swallowed poisoning would land on an ACTIVE transaction.
            await db.execute(text("SELECT 1"))

            with patch.object(store_mod.EntityFactStore, "corroborate", _boom):
                # Must NOT raise (best-effort swallow) ...
                await reconcile_verdict(
                    db,
                    workspace_id=ws,
                    user_id=uid,
                    verdict=VerifyVerdict.CONFIRMED,
                    write_input={"entity_id": eid},
                    write_output={},
                )

            # ... and the outer session must still be usable: the primary op can keep
            # issuing statements and commit. Without the SAVEPOINT this raises
            # PendingRollbackError (wrapped DBAPIError).
            assert (await EntityFactStore(db).get_fact(fid)) is not None
            await db.commit()
