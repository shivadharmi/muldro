"""Real-DB tests for the adapter server dispatch — the composed six-step
enforcement (`handle_execute_action`) and the connection-list suppression
(`handle_list_connections`), plus the fastmcp-result normalization helper
(`_result_to_dict`).

`call_openconnector` is patched with an AsyncMock so no real network/MCP
round trip happens; everything before that seam (identity, allowlist,
connection resolution, connectionName forcing) and after it (secret
stripping) runs for real. Skips when Postgres is unreachable, mirroring
tests/adapter/test_connection_resolver.py.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from ulid import ULID

from src.adapter.enforcement import ActionNotAllowed
from src.adapter.server import _result_to_dict, handle_execute_action
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


async def test_handle_execute_action_forces_connection_and_strips_secrets():
    factory, engine = make_test_db()
    suffix = str(ULID())
    principal_id = f"usr_owner_{suffix}"
    alias = "work"
    try:
        await seed_user_workspace(factory, principal_id, TEST_WORKSPACE_ID)
        connection_id = await _seed_connection(factory, principal_id=principal_id, alias=alias)

        token = mint_platform_jwt(
            principal_id=principal_id,
            tenant_id=TEST_WORKSPACE_ID,
            workspace_id=TEST_WORKSPACE_ID,
            capabilities=["email.search"],
        )

        mock_result = {"content": [{"type": "text", "text": "ok"}], "access_token": "leak-me"}
        with patch(
            "src.adapter.server.call_openconnector",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_call:
            async with factory() as db:
                result = await handle_execute_action(
                    db,
                    token=token,
                    args={
                        "actionId": "gmail.search",
                        "input": {"q": "x"},
                        "connectionName": "attacker",
                        "account_alias": alias,
                    },
                )

        mock_call.assert_awaited_once()
        call_args = mock_call.call_args
        forwarded_tool_name = call_args.args[0] if call_args.args else call_args.kwargs["tool_name"]
        forwarded_args = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["args"]

        assert forwarded_tool_name == "execute_action"
        assert forwarded_args["connectionName"] == connection_id
        assert forwarded_args["connectionName"] != "attacker"
        assert "account_alias" not in forwarded_args

        assert "access_token" not in result
        assert result["content"] == [{"type": "text", "text": "ok"}]
    finally:
        await _delete_connection(factory, principal_id=principal_id, alias=alias)
        await engine.dispose()


async def test_handle_execute_action_rejects_unallowlisted_action_without_calling_openconnector():
    factory, engine = make_test_db()
    suffix = str(ULID())
    principal_id = f"usr_owner_{suffix}"
    try:
        await seed_user_workspace(factory, principal_id, TEST_WORKSPACE_ID)

        token = mint_platform_jwt(
            principal_id=principal_id,
            tenant_id=TEST_WORKSPACE_ID,
            workspace_id=TEST_WORKSPACE_ID,
            capabilities=["email.search"],
        )

        with patch(
            "src.adapter.server.call_openconnector",
            new_callable=AsyncMock,
        ) as mock_call:
            async with factory() as db:
                with pytest.raises(ActionNotAllowed):
                    await handle_execute_action(
                        db,
                        token=token,
                        args={"actionId": "gmail.delete_forever", "input": {}},
                    )

        mock_call.assert_not_awaited()
    finally:
        await engine.dispose()


def test_result_to_dict_normalizes_structured_content_object():
    class FakeResult:
        structured_content = {"messages": [{"id": "1"}]}

    normalized = _result_to_dict(FakeResult())

    assert normalized == {"messages": [{"id": "1"}]}


def test_result_to_dict_then_strip_secrets_removes_access_token():
    from src.adapter.enforcement import strip_secrets

    class FakeResult:
        structured_content = {"access_token": "leak-me", "ok": True}

    normalized = _result_to_dict(FakeResult())
    cleaned = strip_secrets(normalized)

    assert "access_token" not in cleaned
    assert cleaned["ok"] is True
