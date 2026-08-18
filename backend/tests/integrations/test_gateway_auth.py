"""Gmail gateway slice: `auth_provider == "platform_jwt"` mints a fresh
platform JWT and returns it as the outbound BearerAuth.

Covers `UserMCPSessionPool._resolve_auth` directly (unit-level) rather than
going through `get_or_create_session`, since auth resolution is the only
behavior under test here.
"""

from fastmcp.client.auth import BearerAuth

from src.integrations.session_pool import UserMCPSessionPool
from src.orchestrator.platform_jwt import verify_platform_jwt


async def test_platform_jwt_auth_provider_mints_bearer_token():
    """auth_provider=platform_jwt -> BearerAuth wrapping a verifiable platform JWT."""
    pool = UserMCPSessionPool()
    config = {
        "transport": "streamable-http",
        "auth_provider": "platform_jwt",
        "url": "https://vmcp.example.com",
    }

    auth = await pool._resolve_auth("google-workspace", "u1", config)

    assert isinstance(auth, BearerAuth)
    token = auth.token.get_secret_value() if hasattr(auth.token, "get_secret_value") else auth.token
    claims = verify_platform_jwt(token, audience="toolhive-vmcp")
    assert claims["sub"] == "u1"
    assert claims["aud"] == "toolhive-vmcp"


async def test_platform_jwt_tenant_id_matches_workspace_not_user():
    """workspace_id is threaded into the JWT's tenant_id/workspace_id claims.

    This is the identity contract the downstream adapter relies on: connection_map
    rows are keyed by workspace_id, so the JWT's tenant_id must be the workspace_id
    (distinct from the user_id), not the user_id.
    """
    pool = UserMCPSessionPool()
    config = {"transport": "streamable-http", "auth_provider": "platform_jwt"}

    auth = await pool._resolve_auth("google-workspace", "usr_1", config, workspace_id="ws_1")

    token = auth.token.get_secret_value() if hasattr(auth.token, "get_secret_value") else auth.token
    claims = verify_platform_jwt(token, audience="toolhive-vmcp")
    assert claims["sub"] == "usr_1"
    assert claims["tenant_id"] == "ws_1"
    assert claims["workspace_id"] == "ws_1"
