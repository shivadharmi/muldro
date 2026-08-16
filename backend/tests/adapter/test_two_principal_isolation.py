"""P0 acceptance gate — the shared-instance tenant boundary.

Proves that in the shared-instance OpenConnector model (one OpenConnector
instance serving every principal in a tenant), one principal cannot reach
another principal's connection through the composed adapter
(``handle_execute_action`` / ``handle_list_connections``). Both principals
here share ``TEST_WORKSPACE_ID`` as ``tenant_id`` (same shared instance) —
the boundary under test is per-PRINCIPAL, not per-tenant.

Mirrors the real-DB seeding pattern in ``tests/adapter/test_server_dispatch.py``
and ``tests/adapter/test_connection_resolver.py``: skip-if-unreachable probe,
``make_test_db``, ``seed_user_workspace``, ``ConnectionMap`` row construction,
cleanup in ``finally``.

If any test here FAILS, the tenant boundary has a hole — do not weaken these
assertions to make them pass.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from ulid import ULID

from src.adapter.connection_resolver import ConnectionDenied
from src.adapter.server import handle_execute_action, handle_list_connections
from src.config.settings import get_settings
from src.models.connection_map import ConnectionMap
from src.orchestrator.platform_jwt import mint_platform_jwt
from tests.conftest import TEST_WORKSPACE_ID, make_test_db, seed_user_workspace


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


async def _seed_connection(factory, *, principal_id: str, alias: str) -> str:
    connection_id = f"{TEST_WORKSPACE_ID}:{principal_id}:gmail:{alias}"
    async with factory() as db:
        db.add(
            ConnectionMap(
                tenant_id=TEST_WORKSPACE_ID,
                workspace_id=TEST_WORKSPACE_ID,
                principal_id=principal_id,
                provider_id="gmail",
                connection_id=connection_id,
                connection_status="active",
                account_alias=alias,
            )
        )
        await db.commit()
    return connection_id


async def _delete_connection(factory, *, principal_id: str, alias: str) -> None:
    async with factory() as db:
        await db.execute(
            ConnectionMap.__table__.delete().where(
                ConnectionMap.tenant_id == TEST_WORKSPACE_ID,
                ConnectionMap.principal_id == principal_id,
                ConnectionMap.provider_id == "gmail",
                ConnectionMap.account_alias == alias,
            )
        )
        await db.commit()


async def test_bob_cannot_use_alice_connection_by_alias():
    factory, engine = make_test_db()
    suffix = str(ULID())
    alice_id = f"usr_alice_{suffix}"
    bob_id = f"usr_bob_{suffix}"
    alias = "work"
    try:
        await seed_user_workspace(factory, alice_id, TEST_WORKSPACE_ID)
        await seed_user_workspace(factory, bob_id, TEST_WORKSPACE_ID)
        await _seed_connection(factory, principal_id=alice_id, alias=alias)

        bob_token = mint_platform_jwt(
            principal_id=bob_id,
            tenant_id=TEST_WORKSPACE_ID,
            workspace_id=TEST_WORKSPACE_ID,
            capabilities=["email.search"],
        )

        with patch(
            "src.adapter.server.call_openconnector",
            new_callable=AsyncMock,
        ) as mock_call:
            async with factory() as db:
                with pytest.raises(ConnectionDenied):
                    await handle_execute_action(
                        db,
                        token=bob_token,
                        args={
                            "actionId": "gmail.fetch_emails",
                            "input": {},
                            "account_alias": alias,
                        },
                    )

        mock_call.assert_not_awaited()
    finally:
        await _delete_connection(factory, principal_id=alice_id, alias=alias)
        await engine.dispose()


async def test_bob_cannot_enumerate_alice_connections():
    factory, engine = make_test_db()
    suffix = str(ULID())
    alice_id = f"usr_alice_{suffix}"
    bob_id = f"usr_bob_{suffix}"
    alias = "work"
    try:
        await seed_user_workspace(factory, alice_id, TEST_WORKSPACE_ID)
        await seed_user_workspace(factory, bob_id, TEST_WORKSPACE_ID)
        await _seed_connection(factory, principal_id=alice_id, alias=alias)

        bob_token = mint_platform_jwt(
            principal_id=bob_id,
            tenant_id=TEST_WORKSPACE_ID,
            workspace_id=TEST_WORKSPACE_ID,
            capabilities=["email.search"],
        )

        async with factory() as db:
            result = await handle_list_connections(db, token=bob_token)

        assert result["connections"] == []
    finally:
        await _delete_connection(factory, principal_id=alice_id, alias=alias)
        await engine.dispose()


async def test_alice_can_use_her_own_connection():
    factory, engine = make_test_db()
    suffix = str(ULID())
    alice_id = f"usr_alice_{suffix}"
    alias = "work"
    try:
        await seed_user_workspace(factory, alice_id, TEST_WORKSPACE_ID)
        connection_id = await _seed_connection(factory, principal_id=alice_id, alias=alias)

        alice_token = mint_platform_jwt(
            principal_id=alice_id,
            tenant_id=TEST_WORKSPACE_ID,
            workspace_id=TEST_WORKSPACE_ID,
            capabilities=["email.search"],
        )

        mock_result = {"content": [{"type": "text", "text": "ok"}]}
        with patch(
            "src.adapter.server.call_openconnector",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_call:
            async with factory() as db:
                result = await handle_execute_action(
                    db,
                    token=alice_token,
                    args={
                        "actionId": "gmail.fetch_emails",
                        "input": {"q": "x"},
                        "account_alias": alias,
                    },
                )

        mock_call.assert_awaited_once()
        call_args = mock_call.call_args
        forwarded_args = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["args"]

        assert forwarded_args["connectionName"] == connection_id
        assert result["content"] == [{"type": "text", "text": "ok"}]
    finally:
        await _delete_connection(factory, principal_id=alice_id, alias=alias)
        await engine.dispose()
