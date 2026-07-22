"""Step 6A.5 Task 0: the external Filesystem MCP is removed — no filesystem tool seeds,
no filesystem.* capabilities, no filesystem in any agent scope, no filesystem install."""


def test_no_filesystem_tool_seeds():
    from src.tools.catalog import EXTERNAL_TOOL_SEEDS

    names = {s.name for s in EXTERNAL_TOOL_SEEDS}
    for n in (
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "directory_tree",
        "search_files",
        "move_file",
        "create_directory",
        "read_text_file",
        "read_media_file",
        "read_multiple_files",
        "list_directory_with_sizes",
        "get_file_info",
        "list_allowed_directories",
    ):
        assert n not in names, f"{n} still seeded"
    assert not any((getattr(s, "server", "") == "filesystem") for s in EXTERNAL_TOOL_SEEDS), (
        "filesystem server still present in EXTERNAL_TOOL_SEEDS"
    )


def test_no_filesystem_capabilities():
    from src.integrations.capabilities import CAPABILITY_CATALOG

    assert not any(c.startswith("filesystem.") for c in CAPABILITY_CATALOG), (
        "filesystem.* capability keys still present in CAPABILITY_CATALOG"
    )


def test_no_filesystem_in_agent_scope():
    from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

    for agent, scope in AGENT_CAPABILITY_SCOPES.items():
        for cap in scope:
            assert not cap.startswith("filesystem."), (
                f"Agent '{agent}' still has filesystem capability: {cap}"
            )
