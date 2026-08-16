"""Real-HTTP e2e: platform JWT -> adapter /mcp -> OpenConnector (hackernews).

The "it actually runs" gate. Drives a real action through the FULL adapter
boundary over HTTP against a live OpenConnector, and asserts: a real result
comes back (a genuine hackernews payload, not an error envelope), and a second
principal cannot use the first's connection. It also sanity-checks that no
secret-named keys appear in the response — but note the no-auth `hackernews`
payload carries no secrets, so that check is a no-regression guard here; the
``strip_secrets`` path itself is exercised by the adapter unit tests.

Runs only when the stack is up (conftest gates on adapter :8100) AND the test
process shares the adapter's platform-JWT PEM (so minted tokens verify). The
adapter runs under the `hackernews` profile (JARVIS_GATEWAY_PROVIDER); the
`hackernews:default` virtual connection is no-auth, so no OAuth is needed.

NOTE (spike §8): hackernews's connectionName is the plain `"default"`, so this
does NOT exercise the multi-colon namespaced Gmail connectionName round-trip —
that remains a real-Gmail (runbook) verification.
"""

import os

import pytest
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from ulid import ULID

from src.models.connection_map import ConnectionMap
from src.orchestrator.platform_jwt import mint_platform_jwt
from tests.conftest import TEST_WORKSPACE_ID, make_test_db, seed_user_workspace

_ADAPTER_MCP = "http://127.0.0.1:8100/mcp"
_HN_ACTION = "hackernews.get_ask_stories"
# the hackernews virtual connection's connectionName (spike-confirmed)
_HN_CONNECTION_ID = "default"

pytestmark = pytest.mark.skipif(
    not os.environ.get("JARVIS_PLATFORM_JWT_PRIVATE_PEM"),
    reason="test process must share the adapter's platform-JWT PEM to mint verifiable tokens",
)


async def _seed_hn(factory, principal_id, connection_id):
    async with factory() as db:
        db.add(
            ConnectionMap(
                tenant_id=TEST_WORKSPACE_ID,
                workspace_id=TEST_WORKSPACE_ID,
                principal_id=principal_id,
                provider_id="hackernews",
                connection_id=connection_id,
                connection_status="active",
                account_alias="default",
            )
        )
        await db.commit()


async def _delete(factory, principal_id):
    async with factory() as db:
        await db.execute(
            ConnectionMap.__table__.delete().where(ConnectionMap.principal_id == principal_id)
        )
        await db.commit()


def _token(principal_id):
    return mint_platform_jwt(
        principal_id=principal_id,
        tenant_id=TEST_WORKSPACE_ID,
        workspace_id=TEST_WORKSPACE_ID,
        capabilities=["hackernews.read"],
    )


async def _execute(token, action_id):
    async with Client(_ADAPTER_MCP, auth=BearerAuth(token=token)) as client:
        return await client.call_tool("execute_action", {"actionId": action_id, "input": {}})


def _payload(result):
    for attr in ("structured_content", "structuredContent", "data"):
        val = getattr(result, attr, None)
        if isinstance(val, dict):
            return val
    content = getattr(result, "content", None)
    if content:
        return {"text": " ".join(getattr(b, "text", "") for b in content)}
    return {"raw": str(result)}


async def test_real_execute_action_and_two_principal_isolation():
    factory, engine = make_test_db()
    suffix = str(ULID())
    alice = f"usr_alice_{suffix}"
    bob = f"usr_bob_{suffix}"
    try:
        await seed_user_workspace(factory, alice, TEST_WORKSPACE_ID)
        await seed_user_workspace(factory, bob, TEST_WORKSPACE_ID)
        await _seed_hn(factory, alice, _HN_CONNECTION_ID)
        # Bob deliberately owns NO hackernews connection.

        # (a) Alice gets a REAL result from OpenConnector through the adapter.
        # `story_ids` is the success marker of hackernews.get_ask_stories — an
        # error envelope ({"ok": false, ...}) would NOT contain it, so this
        # can't go green on a failed call.
        result = await _execute(_token(alice), _HN_ACTION)
        payload = _payload(result)
        flat = str(payload).lower()
        assert "story_ids" in flat, f"expected a real HN payload, got {payload!r}"
        assert '"ok": false' not in flat and "'ok': false" not in flat, payload

        # (b) No secret-named keys in the response (no-regression guard — the HN
        # payload is secret-free; strip_secrets itself is unit-tested).
        normalized = flat.replace("_", "").replace("-", "")
        for secret in ("accesstoken", "refreshtoken", "clientsecret", "authorization", "apikey"):
            assert secret not in normalized

        # (c) Bob is denied — no connection of his own (adapter is the boundary).
        # Assert the denial is specifically the connection refusal, not any
        # incidental error (network, token) that would also raise.
        with pytest.raises(Exception) as denied:
            await _execute(_token(bob), _HN_ACTION)
        assert "connection" in str(denied.value).lower(), denied.value
    finally:
        await _delete(factory, alice)
        await _delete(factory, bob)
        await engine.dispose()
