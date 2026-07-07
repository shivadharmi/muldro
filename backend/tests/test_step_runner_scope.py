"""Step 6C Task 3.3: the autonomous executor offers ONLY the current step's
capability tools, NOT the executor's full write union.

This is the per-step scope security win of "kill Operator": today an ``email.send``
step is also offered ``calendar.create``'s tool (the whole write union). After the
change, ``StepRunner.build_executor_tools(step_capability, workspace_id)`` delegates
to the workspace-scoped ``CapabilityResolver.resolve_for_step`` so the step is offered
its own capability's primary tool + same-family read-only tools ONLY.

Real-DB proof (no Docker/Anthropic dependency): skips (does not fail) when Postgres is
unreachable, mirroring ``tests/test_approval_idempotency_constraint.py``. Each test
builds its own engine bound to its own event loop (this repo's custom async-test hook
runs every test via a fresh ``asyncio.run``) and disposes it in a ``finally``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.tool_definitions import ToolDefinition
from src.models.users import User, Workspace
from src.services.step_runner import StepRunner


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip.

    Mirrors ``tests/test_approval_idempotency_constraint.py``: a raw asyncpg connect on
    its own throwaway loop, never touching the app's process-wide cached engine.
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
    """Yield ``(factory, workspace_id)`` with an FK-parent User + Workspace and three
    enabled ToolDefinition rows seeded in that workspace:

      - ``send_email``  capability ``email.send``    requires_approval=True  (step's own)
      - ``list_email``  capability ``email.list``    requires_approval=False (same family)
      - ``create_event`` capability ``calendar.create`` requires_approval=True (other fam)

    Teardown deletes the tools, then the Workspace + User, then disposes the engine.
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
                    email=f"scope-{suffix}@example.com",
                    display_name="scope-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="scope-ws", owner_user_id=user_id))
            await db.commit()
        async with factory() as db:
            db.add(
                ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name="send_email",
                    capability="email.send",
                    requires_approval=True,
                    enabled=True,
                    input_schema={"type": "object"},
                )
            )
            db.add(
                ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name="list_email",
                    capability="email.list",
                    requires_approval=False,
                    enabled=True,
                    input_schema={"type": "object"},
                )
            )
            db.add(
                ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name="create_event",
                    capability="calendar.create",
                    requires_approval=True,
                    enabled=True,
                    input_schema={"type": "object"},
                )
            )
            await db.commit()
        yield factory, workspace_id
    finally:
        try:
            async with factory() as db:
                await db.execute(
                    delete(ToolDefinition).where(ToolDefinition.workspace_id == workspace_id)
                )
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


def _minimal_runner(factory) -> StepRunner:
    """Build a minimal StepRunner whose ``build_executor_tools`` can run.

    After the Step 6C change that method reads ONLY ``self._db_factory`` (a property that
    resolves ``self._db_factory_provider()``), so we bypass ``__init__`` and wire just the
    provider — mirroring how the other StepRunner tests build minimal instances.
    """
    runner = StepRunner.__new__(StepRunner)
    runner._db_factory_provider = lambda: factory
    return runner


async def test_executor_tools_scoped_to_step_capability_only():
    """``email.send`` step → offered ``send_email`` (own) + ``list_email`` (same-family
    read) but NEVER ``create_event`` (a DIFFERENT capability's write tool)."""
    async with _env() as (factory, workspace_id):
        runner = _minimal_runner(factory)

        tools = await runner.build_executor_tools("email.send", workspace_id)
        names = {t["name"] for t in tools}

        assert "send_email" in names  # the step's own capability tool
        assert "list_email" in names  # same-family read-only tool (related read)
        assert "create_event" not in names  # NEGATIVE control: no cross-capability leak
