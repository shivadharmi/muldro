"""Step 6C Task 3.5: real-DB proof of the drop-operator-agent-row migration.

Proves the seed -> stray-operator -> DELETE lifecycle that migration
``574f6c145bca`` performs. ``AgentRegistry.seed_defaults()`` seeds the 7 GLOBAL
agents from ``AGENT_PROMPTS`` — which contains ``executor`` and NO ``operator`` since
the rename — and NEVER deletes. A pre-rename DB may still carry a stray ``operator``
row; the migration's one-line ``DELETE FROM agents WHERE ... name = 'operator'`` removes
it. Here we assert the DELETE's effect directly (the migration is a single DELETE); the
alembic down/up round-trip is verified manually against the live DB, not in-process.

The ``agents`` table is GLOBAL (no ``workspace_id`` — see the multi-tenant doc), so this
test is careful to delete ONLY the agent rows it created (the stray ``operator`` plus any
of the 7 defaults it seeded) in teardown, leaving no pollution.

No Docker/Anthropic dependency: skips (does not fail) when Postgres is unreachable,
mirroring ``tests/test_deep_gate_end_to_end.py``. Each test builds its own NullPool
engine bound to its own event loop (this repo's custom async-test hook runs every test
via a fresh ``asyncio.run``) and disposes it in a ``finally``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.agents import Agent
from src.services.agent_registry import AgentRegistry

# The exact DELETE the migration (574f6c145bca) executes.
_MIGRATION_DELETE = "DELETE FROM agents WHERE agent_id = 'operator' OR name = 'operator'"


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
async def _agents_env():
    """Yield an ``async_sessionmaker`` over a fresh NullPool engine on this test's loop.

    Teardown deletes ONLY the agent rows this test created — the 7 seeded defaults plus
    the stray ``operator`` — then disposes the engine. The ``agents`` table is global, so
    we scope deletion to the exact names we touched to avoid clobbering any pre-existing
    agent rows that happened to already be present.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Names we may create: the 7 seeded defaults + the stray operator.
    from src.orchestrator.prompts import AGENT_PROMPTS

    touched_names = set(AGENT_PROMPTS.keys()) | {"operator"}
    # Snapshot which of those names already exist so we leave pre-existing rows intact.
    async with factory() as db:
        pre = await db.execute(select(Agent.name).where(Agent.name.in_(touched_names)))
        preexisting = {row[0] for row in pre.all()}
    try:
        yield factory, preexisting
    finally:
        try:
            async with factory() as db:
                # Delete only the names we created (not ones present before the test).
                to_delete = touched_names - preexisting
                if to_delete:
                    await db.execute(delete(Agent).where(Agent.name.in_(to_delete)))
                    await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


async def _count_by_name(factory, name: str) -> int:
    async with factory() as db:
        result = await db.execute(select(func.count()).select_from(Agent).where(Agent.name == name))
        return int(result.scalar_one())


async def test_migration_delete_removes_operator_and_keeps_executor():
    """seed_defaults creates executor (not operator); migration DELETE drops a stray operator."""
    async with _agents_env() as (factory, preexisting):
        # 1. Seed the 7 default agents — creates ``executor``, never ``operator``.
        async with factory() as db:
            await AgentRegistry(db).seed_defaults()
            await db.commit()

        assert await _count_by_name(factory, "executor") == 1
        assert await _count_by_name(factory, "operator") == 0

        # 2. Manually insert a stray ``operator`` row (simulating a pre-rename seed).
        async with factory() as db:
            db.add(
                Agent(
                    agent_id=f"agt_{ULID()}",
                    name="operator",
                    display_name="Operator",
                    system_prompt="stale pre-rename operator prompt",
                    model_tier="sonnet",
                    capability_scope=["email.send"],
                    max_tokens=4096,
                    temperature=0.3,
                    enabled=True,
                )
            )
            await db.commit()

        assert await _count_by_name(factory, "operator") == 1  # stray present pre-migration

        # 3. Run the EXACT DELETE the migration (574f6c145bca) executes.
        async with factory() as db:
            await db.execute(text(_MIGRATION_DELETE))
            await db.commit()

        # 4. Assert: operator gone, executor untouched.
        assert await _count_by_name(factory, "operator") == 0
        assert await _count_by_name(factory, "executor") == 1


async def test_migration_delete_is_idempotent_when_no_operator():
    """With no stray operator, the migration DELETE is a safe no-op (idempotency)."""
    async with _agents_env() as (factory, preexisting):
        async with factory() as db:
            await AgentRegistry(db).seed_defaults()
            await db.commit()

        assert await _count_by_name(factory, "operator") == 0

        # Running the migration DELETE against a DB with no operator row changes nothing.
        async with factory() as db:
            await db.execute(text(_MIGRATION_DELETE))
            await db.commit()

        assert await _count_by_name(factory, "operator") == 0
        assert await _count_by_name(factory, "executor") == 1
