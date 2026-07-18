"""Real-DB tenant-isolation test for ``ToolRegistry.list_tools(workspace_scoped=...)``.

`list_tools` was workspace-AGNOSTIC (filtered only connector_type + enabled), so a
workspace-specific ToolDefinition belonging to another tenant could leak into a caller that
builds an agent's callable tool set (``tool_executor``). The new opt-in ``workspace_scoped``
applies the SAME tenant bound as ``get_tool``: this-workspace rows + the global (NULL) catalog.
Default OFF stays byte-identical for the name-resolution callers that re-scope at dispatch.

Self-contained real-Postgres test (skips when unreachable), mirroring the DB-gate tests: own
throwaway engine + NullPool, seed User→2 Workspaces→3 ToolDefinitions, assert scoping, teardown.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest


def _db_reachable() -> bool:
    import asyncpg

    from src.config.settings import get_settings

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


@asynccontextmanager
async def _scope_env():
    """Seed User + two Workspaces + 3 ToolDefinitions (global / ws_A / ws_B) sharing a
    connector_type + capability, so ONLY the workspace bound distinguishes them. Yields
    ``(factory, ws_a, ws_b, names)``; teardown deletes the tools then the workspaces + user."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from ulid import ULID

    from src.config.settings import get_settings
    from src.models.tool_definitions import ToolDefinition
    from src.models.users import User, Workspace

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    ws_a = f"wsA_{suffix}"
    ws_b = f"wsB_{suffix}"
    names = {
        "global": f"tool_global_{suffix}",
        "a": f"tool_a_{suffix}",
        "b": f"tool_b_{suffix}",
    }
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"ts-{suffix}@example.com", display_name="ts"))
            db.add(Workspace(workspace_id=ws_a, name="A", owner_user_id=user_id))
            db.add(Workspace(workspace_id=ws_b, name="B", owner_user_id=user_id))
            await db.flush()
            for key, ws in (("global", None), ("a", ws_a), ("b", ws_b)):
                db.add(
                    ToolDefinition(
                        tool_id=f"tool_{ULID()}",
                        workspace_id=ws,
                        name=names[key],
                        connector_type="slack",
                        capability="chat.post_message",
                        enabled=True,
                    )
                )
            await db.commit()
        yield factory, ws_a, ws_b, names
    finally:
        try:
            async with factory() as db:
                await db.execute(
                    delete(ToolDefinition).where(ToolDefinition.name.in_(list(names.values())))
                )
                await db.execute(delete(Workspace).where(Workspace.workspace_id.in_([ws_a, ws_b])))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
async def test_workspace_scoped_drops_other_tenant_keeps_global_and_own():
    from src.services.tool_registry import ToolRegistry

    async with _scope_env() as (factory, ws_a, ws_b, names):
        async with factory() as db:
            got = {
                t.name
                for t in await ToolRegistry(db, ws_a).list_tools(
                    connector_type="slack", workspace_scoped=True
                )
            }
        # ws_A sees the global catalog + its OWN row, never ws_B's.
        assert names["global"] in got
        assert names["a"] in got
        assert names["b"] not in got


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
async def test_default_unscoped_returns_all_tenants_byte_identical():
    """Default workspace_scoped=False keeps the historical workspace-AGNOSTIC behavior — all
    three rows, regardless of the registry's workspace_id (the byte-neutral guarantee for the
    untouched name-resolution callers)."""
    from src.services.tool_registry import ToolRegistry

    async with _scope_env() as (factory, ws_a, ws_b, names):
        async with factory() as db:
            got = {t.name for t in await ToolRegistry(db, ws_a).list_tools(connector_type="slack")}
        assert {names["global"], names["a"], names["b"]} <= got


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
async def test_workspace_agnostic_registry_scoped_sees_global_only():
    """A registry with no workspace_id + workspace_scoped=True sees ONLY global rows (mirrors
    get_tool's else-branch)."""
    from src.services.tool_registry import ToolRegistry

    async with _scope_env() as (factory, ws_a, ws_b, names):
        async with factory() as db:
            got = {
                t.name
                for t in await ToolRegistry(db, None).list_tools(
                    connector_type="slack", workspace_scoped=True
                )
            }
        assert names["global"] in got
        assert names["a"] not in got
        assert names["b"] not in got
