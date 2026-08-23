import inspect
import re

import src.integrations.seed_installations as seed_mod
from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS


def _by_name(name: str) -> dict:
    return next(i for i in _DEFAULT_INSTALLATIONS if i["server_name"] == name)


def test_github_is_remote_http_not_docker():
    # github is gateway-routed (Wave 3): no static remote_url, no local
    # process, no docker — the OpenConnector adapter resolves the endpoint.
    gh = _by_name("github")
    assert gh["transport"] == "streamable-http"
    assert gh["remote_url"] is None
    assert gh["command"] is None
    assert gh["args"] is None
    assert "docker" not in str(gh.get("args"))


def test_google_workspace_seed_is_gateway_routed_not_managed_local():
    # google-workspace is gateway-routed (Wave 3): no uvx-managed local
    # process, no static remote_url — the OpenConnector adapter resolves it.
    gw = _by_name("google-workspace")
    assert not gw.get("managed_local")
    assert gw["remote_url"] is None
    assert gw["transport"] == "streamable-http"


def test_http_schemas_not_cleared_on_seed():
    src = inspect.getsource(seed_mod.seed_installations)
    assert "_clear_stale_tool_schemas(db, server_name, workspace_id)" not in src


def _stdio_installations() -> list[dict]:
    """Every seeded installation still launched as a local child process.

    Derived rather than named: a provider that migrates to the gateway loses its
    `args` entirely, and a hardcoded name list turns that success into a
    TypeError on `None`. What must stay true is a property of whatever remains
    stdio, not of any particular brand.
    """
    return [i for i in _DEFAULT_INSTALLATIONS if i["transport"] == "stdio" and i.get("args")]


def test_npx_servers_are_version_pinned():
    stdio = _stdio_installations()
    for inst in stdio:
        name = inst["server_name"]
        pkg = next(
            (a for a in inst["args"] if not a.startswith("-") and "@" in a),
            None,
        )
        assert pkg, f"{name} should have an npx package arg"
        # The version segment is an @<digit...> AFTER the package name. For
        # scoped packages (@scope/name@1.2.3) strip the leading scope first.
        tail = pkg[1:] if pkg.startswith("@") else pkg
        assert re.search(r"@\d", tail), f"{name} npx package '{pkg}' is not version-pinned"


def test_migrated_installations_declare_platform_jwt():
    for name in ("google-workspace", "github", "notion"):
        inst = _by_name(name)
        assert inst["auth_provider"] == "platform_jwt"
        assert inst["transport"] == "streamable-http"


def test_migrated_installations_carry_no_native_transport_config():
    for name in ("google-workspace", "github"):
        inst = _by_name(name)
        assert inst.get("command") is None
        assert inst.get("remote_url") is None
        assert not inst.get("managed_local")


def test_unmigrated_installations_keep_native_transport():
    """Slack is the last native installation, and only because OC lacks a client.

    Atlassian left this test when it migrated: its remote_url addressed
    Atlassian's own Rovo MCP server, which the gateway replaces. Slack cannot
    follow until a Slack OAuth app exists — MULDRO_SLACK_OAUTH_CLIENT_ID has no
    setting and no value, and adding slack to PROVIDER_REGISTRY without one
    makes register_gateway_oauth_configs abort startup.
    """
    assert _by_name("slack")["command"] == "npx"
    assert _by_name("slack")["transport"] == "stdio"
