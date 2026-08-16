"""Gmail gateway slice: a cached platform-JWT session refreshes before its
bearer expires.

The minted platform JWT lives 300s, but it is bound to an MCP client session
at creation. ``get_or_create_session`` returns an existing entry without
re-checking platform-token expiry (it only does this for OAuth servers), so a
turn lasting longer than the TTL would reuse an expired bearer and the gateway
would reject the call. These tests pin the fix: a near-expiry platform-JWT
session is rebuilt (fresh token) on reuse, while a still-valid one is reused
unchanged (no needless rebuild on every call).
"""

import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.session_pool import UserMCPSessionPool

_SERVER = "google-workspace"
_WS = "ws_1"
_USER = "u1"
_GATEWAY_CONFIG = {
    "transport": "streamable-http",
    "auth_provider": "platform_jwt",
    "url": "https://vmcp.example.com",
}


def _fake_client_ctx(*_a, **_k):
    fake_client = AsyncMock()
    fake_client.list_tools = AsyncMock(return_value=[])
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)
    return fake_ctx


@contextmanager
def _mocked_client(pool):
    with (
        patch("src.integrations.session_pool.Client", MagicMock(side_effect=_fake_client_ctx)),
        patch.object(pool, "_register_discovered_tools", AsyncMock()),
    ):
        yield


async def _session(pool):
    return await pool.get_or_create_session(_SERVER, user_id=_USER, workspace_id=_WS)


async def test_near_expiry_platform_jwt_session_is_rebuilt_on_reuse():
    pool = UserMCPSessionPool()
    pool.register_server_config(_SERVER, _GATEWAY_CONFIG, workspace_id=_WS)

    with _mocked_client(pool):
        first = await _session(pool)
        first_token = first.bound_token

        # Simulate the bound JWT being on the verge of expiry.
        key = (_WS, _SERVER, _USER)
        pool._sessions[key].bound_token_exp = time.time() + 1  # within the refresh margin

        second = await _session(pool)

    assert second.bound_token is not None
    assert second.bound_token != first_token, "expected a freshly minted JWT after refresh"


async def test_valid_platform_jwt_session_is_reused_without_rebuild():
    pool = UserMCPSessionPool()
    pool.register_server_config(_SERVER, _GATEWAY_CONFIG, workspace_id=_WS)

    with _mocked_client(pool):
        first = await _session(pool)
        # Fresh 300s token — nowhere near the expiry margin.
        second = await _session(pool)

    assert second.bound_token == first.bound_token, "a still-valid session must not be rebuilt"
