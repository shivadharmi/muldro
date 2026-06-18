import inspect
import re

import src.integrations.seed_installations as seed_mod
from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS


def _by_name(name: str) -> dict:
    return next(i for i in _DEFAULT_INSTALLATIONS if i["server_name"] == name)


def test_github_is_remote_http_not_docker():
    gh = _by_name("github")
    assert gh["transport"] == "streamable-http"
    assert gh["remote_url"] == "https://api.githubcopilot.com/mcp/"
    assert gh["command"] is None
    assert gh["args"] is None
    assert gh["auth_provider"] == "github"
    assert "docker" not in str(gh.get("args"))


def test_google_workspace_seed_is_managed_local():
    gw = _by_name("google-workspace")
    assert gw["managed_local"] is True
    assert gw["remote_url"] is None
    assert gw["transport"] == "streamable-http"


def test_http_schemas_not_cleared_on_seed():
    src = inspect.getsource(seed_mod.seed_installations)
    assert "_clear_stale_tool_schemas(db, server_name, workspace_id)" not in src


def test_npx_servers_are_version_pinned():
    for name in ("slack", "playwright", "filesystem", "notion"):
        inst = _by_name(name)
        pkg = next(
            (a for a in inst["args"] if not a.startswith("-") and "@" in a),
            None,
        )
        assert pkg, f"{name} should have an npx package arg"
        # The version segment is an @<digit...> AFTER the package name. For
        # scoped packages (@scope/name@1.2.3) strip the leading scope first.
        tail = pkg[1:] if pkg.startswith("@") else pkg
        assert re.search(r"@\d", tail), f"{name} npx package '{pkg}' is not version-pinned"
