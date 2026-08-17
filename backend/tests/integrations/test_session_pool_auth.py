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

from fastmcp.client.auth import BearerAuth

from src.integrations.gateway_actions import capabilities_for_server
from src.integrations.mcp_errors import McpAuthRequiredError
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


async def test_platform_jwt_branch_returns_a_bearer_without_raising_reauth():
    """The empty ``_PROVIDER_SOURCES`` in ``provider_map`` rests on THIS property.

    Wave E retired the ``google -> [gmail, calendar]`` fan-out entry from
    ``src.integrations.provider_map._PROVIDER_SOURCES``. That is safe only
    because ``sources_for_provider`` is reachable solely through
    ``McpAuthRequiredError`` -> ``ReauthService.apply_needs_reauth``, and a
    gateway installation authenticates with a platform JWT — whose branch is
    the FIRST in ``_resolve_auth`` and returns a ``BearerAuth``
    unconditionally, before the OAuth branch that owns both raise sites.

    The property is one of branch ORDER, so nothing else pins it. If a future
    change lets the ``platform_jwt`` branch raise (an expired gateway
    connection is the natural wish), ``dag_runner._defer_for_reauth`` would
    resolve ``google-workspace`` -> ``google`` -> ``["google"]`` instead of
    ``["gmail", "calendar"]``: gmail and calendar perception would keep
    polling a dead connection and never be paused, silently. Follow
    ``provider_map``'s in-code restore instruction (re-add the ``google``
    entry) before making that change.
    """
    pool = UserMCPSessionPool()

    try:
        auth = await pool._resolve_auth(
            "google-workspace", _USER, dict(_GATEWAY_CONFIG), workspace_id=_WS
        )
    except McpAuthRequiredError as exc:  # pragma: no cover - the guard's whole point
        raise AssertionError(
            "the platform_jwt branch of _resolve_auth raised McpAuthRequiredError; "
            "restore _PROVIDER_SOURCES['google'] = ['gmail', 'calendar'] in "
            "src/integrations/provider_map.py or gmail/calendar perception will "
            "never be paused for re-auth"
        ) from exc

    assert isinstance(auth, BearerAuth)


async def test_unknown_gateway_server_is_logged_as_an_error(caplog):
    """The two gateway-ness signals disagreeing must not be silent.

    An installation that declares platform_jwt (so it ROUTES to the vMCP) but
    whose server_name the registry does not know mints an empty capability set,
    so every call it makes is denied at the adapter — a useless installation.
    Minting still succeeds (no raise); the error log is the only signal.
    """
    pool = UserMCPSessionPool()

    with caplog.at_level("ERROR", logger="src.integrations.session_pool"):
        auth = await pool._resolve_auth(
            "totally-unregistered-server", _USER, dict(_GATEWAY_CONFIG), workspace_id=_WS
        )

    assert auth is not None
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("totally-unregistered-server" in m and "platform_jwt" in m for m in errors), errors
