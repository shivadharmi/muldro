"""Ambiguous tool->server resolution must be deterministic and observable."""

import logging

from src.integrations.session_pool import UserMCPSessionPool


def _pool_with_collision() -> UserMCPSessionPool:
    """Two servers in one workspace both advertising 'shared_tool'.

    Insertion order puts 'zeta' first so a first-match-wins implementation
    returns 'zeta' and a sorted implementation returns 'alpha' — the two are
    distinguishable.
    """
    pool = UserMCPSessionPool()
    pool._server_tools[("ws_1", "zeta")] = {"shared_tool": "shared_tool"}
    pool._server_tools[("ws_1", "alpha")] = {"shared_tool": "shared_tool"}
    return pool


def test_ambiguous_tool_resolves_deterministically():
    pool = _pool_with_collision()
    assert pool.get_server_for_tool("shared_tool", workspace_id="ws_1") == "alpha"


def test_ambiguous_tool_logs_a_warning_naming_every_candidate(caplog):
    pool = _pool_with_collision()
    with caplog.at_level(logging.WARNING):
        pool.get_server_for_tool("shared_tool", workspace_id="ws_1")
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "an ambiguous resolution produced no warning"
    joined = " ".join(warnings)
    assert "shared_tool" in joined
    assert "alpha" in joined and "zeta" in joined


def test_unambiguous_tool_is_silent():
    """The warning must fire only on a real collision, not on every lookup."""
    pool = UserMCPSessionPool()
    pool._server_tools[("ws_1", "only")] = {"lonely_tool": "lonely_tool"}
    import logging as _logging

    records: list[_logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = _logging.getLogger("src.integrations.session_pool")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        assert pool.get_server_for_tool("lonely_tool", workspace_id="ws_1") == "only"
    finally:
        logger.removeHandler(handler)
    assert not [r for r in records if r.levelno >= _logging.WARNING]


def _warnings_during(fn):
    """Run ``fn``, returning its result plus WARNING+ records from the pool logger."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("src.integrations.session_pool")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        result = fn()
    finally:
        logger.removeHandler(handler)
    return result, [r for r in records if r.levelno >= logging.WARNING]


def test_same_server_in_two_workspaces_is_not_a_collision():
    """An unscoped lookup walks every workspace — one server twice is not ambiguity.

    Regression: candidates were collected without dedup, so a server installed
    in two workspaces yielded ["slack", "slack"], tripped the ambiguity branch,
    and named the same server twice. notifier.py resolves with workspace_id=""
    whenever the notification payload omits the key, so a two-workspace
    deployment logged a phantom collision on every delivery — noise on the
    exact channel the warning exists to keep clean.
    """
    pool = UserMCPSessionPool()
    pool._server_tools[("ws_1", "slack")] = {"slack_send_message": "slack_send_message"}
    pool._server_tools[("ws_2", "slack")] = {"slack_send_message": "slack_send_message"}

    resolved, warnings = _warnings_during(
        lambda: pool.get_server_for_tool("slack_send_message", workspace_id="")
    )
    assert resolved == "slack"
    assert not warnings, f"non-collision warned: {[r.getMessage() for r in warnings]}"


def test_missing_tool_still_returns_none():
    pool = _pool_with_collision()
    assert pool.get_server_for_tool("absent_tool", workspace_id="ws_1") is None


def test_unregister_one_server_keeps_another_servers_same_named_tool():
    """Revoking server A must not delete server B's metadata for a shared name.

    Regression: _tool_metadata was keyed by bare tool_name, so two servers
    serving one name overwrote each other, and unregister_server popped by that
    bare name — leaving B advertising a tool whose schema was gone.
    """
    pool = UserMCPSessionPool()
    pool._server_tools[("ws_1", "alpha")] = {"shared_tool": "shared_tool"}
    pool._server_tools[("ws_1", "beta")] = {"shared_tool": "shared_tool"}
    for server in ("alpha", "beta"):
        pool._tool_metadata[("ws_1", server, "shared_tool")] = {
            "name": "shared_tool",
            "server": server,
            "description": f"{server} version",
            "input_schema": {"type": "object", "properties": {}},
            "_workspace_id": "ws_1",
        }

    pool.unregister_server("alpha", workspace_id="ws_1")

    surviving = pool.get_all_tool_metadata(workspace_id="ws_1")
    servers = {m["server"] for m in surviving if m["name"] == "shared_tool"}
    assert servers == {"beta"}, f"expected only beta to survive, got {servers}"


def test_two_servers_same_tool_name_coexist_in_metadata():
    pool = UserMCPSessionPool()
    for server in ("alpha", "beta"):
        pool._tool_metadata[("ws_1", server, "shared_tool")] = {
            "name": "shared_tool",
            "server": server,
            "description": f"{server} version",
            "input_schema": {"type": "object", "properties": {}},
            "_workspace_id": "ws_1",
        }
    got = pool.get_all_tool_metadata(workspace_id="ws_1")
    assert sorted(m["server"] for m in got) == ["alpha", "beta"]
    # The re-key must still hand consumers a plain string name. tool_executor
    # keys its schema dict by this value; if it leaked the tuple key, every
    # external tool schema would silently become unfindable.
    assert all(m["name"] == "shared_tool" for m in got)


def test_unscoped_metadata_still_strips_injected_params():
    """An unscoped read must strip tool_defaults using the row's OWN workspace.

    Regression: the _server_configs lookup used the caller's workspace_id, so
    workspace_id="" missed ("", server) entirely and cloudId survived in the
    schema as required — the exact "agent asks the user for a value the server
    already knows" failure the stripping exists to prevent.
    """
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "atlassian",
        {"tool_defaults": {"cloudId": "cloud-1"}},
        workspace_id="ws_1",
    )
    pool._tool_metadata[("ws_1", "atlassian", "jira_search")] = {
        "name": "jira_search",
        "server": "atlassian",
        "description": "",
        "input_schema": {
            "type": "object",
            "properties": {"cloudId": {"type": "string"}, "jql": {"type": "string"}},
            "required": ["cloudId", "jql"],
        },
        "_workspace_id": "ws_1",
    }

    (item,) = pool.get_all_tool_metadata(workspace_id="")
    schema = item["input_schema"]
    assert "cloudId" not in schema["properties"], "injected param leaked into the agent schema"
    assert schema["required"] == ["jql"]
