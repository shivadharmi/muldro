"""Gateway platform-JWT capability minting: `_resolve_auth`'s `platform_jwt`
branch must derive each installation's capability set from the gateway_actions
registry, not a hardcoded Gmail-only list.

The registry (`src.integrations.gateway_actions.capabilities_for_server`) is
the single source of truth for what capabilities a Jarvis installation
serves: `google-workspace` -> gmail + googlecalendar (email.* + calendar.*),
`github` -> github (issue.* + repo.* + search-ish repo capabilities). These
sets are disjoint. The platform JWT's `capabilities` claim IS the
installation boundary the gateway adapter's `ensure_capability_allowed` gate
checks against, so a session minted for one installation must never carry
another installation's capabilities -- and an unregistered installation must
mint no capabilities at all (fail-closed).
"""

from src.integrations.gateway_actions import capabilities_for_server
from src.integrations.session_pool import UserMCPSessionPool
from src.orchestrator.platform_jwt import DEFAULT_AUDIENCE, verify_platform_jwt

_WS = "ws_1"
_USER = "usr_1"
_GATEWAY_CONFIG = {
    "transport": "streamable-http",
    "auth_provider": "platform_jwt",
    "url": "https://vmcp.example.com",
}


def _claims_from_auth(auth):
    token = auth.token.get_secret_value() if hasattr(auth.token, "get_secret_value") else auth.token
    return verify_platform_jwt(token, audience=DEFAULT_AUDIENCE)


async def test_google_session_token_carries_google_capabilities_only():
    pool = UserMCPSessionPool()

    auth = await pool._resolve_auth(
        "google-workspace", _USER, dict(_GATEWAY_CONFIG), workspace_id=_WS
    )

    claims = _claims_from_auth(auth)
    caps = set(claims["capabilities"])

    assert caps == set(capabilities_for_server("google-workspace"))
    # The old hardcoded list could never produce a calendar.* capability.
    assert any(cap.startswith("calendar.") for cap in caps)


async def test_github_session_token_carries_no_email_capability():
    pool = UserMCPSessionPool()

    auth = await pool._resolve_auth("github", _USER, dict(_GATEWAY_CONFIG), workspace_id=_WS)

    claims = _claims_from_auth(auth)
    caps = set(claims["capabilities"])

    assert caps
    assert any(cap.startswith("issue.") or cap.startswith("repo.") for cap in caps)
    assert not any(cap.startswith("email.") for cap in caps)


async def test_unknown_gateway_server_mints_no_capabilities():
    pool = UserMCPSessionPool()

    auth = await pool._resolve_auth(
        "totally-unregistered-server", _USER, dict(_GATEWAY_CONFIG), workspace_id=_WS
    )

    claims = _claims_from_auth(auth)

    assert claims["capabilities"] == []
