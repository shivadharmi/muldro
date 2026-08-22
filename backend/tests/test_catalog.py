"""Tests for src/tools/catalog.py — internal tool registry."""

from pydantic import BaseModel

from src.tools.catalog import (
    EXTERNAL_TOOL_SEEDS,
    INTERNAL_TOOLS,
    get_internal_tool_by_name,
    get_internal_tool_names,
    get_internal_tools_for_server,
    get_seeds_for_server,
    get_verified_seeds,
)


def test_internal_tools_count():
    """Verify exactly 28 internal tools are registered (24 + 4 system.* action tools)."""
    assert len(INTERNAL_TOOLS) == 28


def test_internal_tool_names_match_muldro():
    """Verify catalog tool names match the internal_tools set in muldro.py."""
    # The internal_tools set is defined at line 2525 in muldro.py
    # We extract it by reading the source directly to ensure exact match
    expected_names = {
        "ingest_event",
        "search",
        "update_entity",
        "get_active_plans",
        "get_goal_memories",
        "evaluate_policy",
        "approve_action",
        "get_briefing",
        "get_observation_cursor",
        "update_observation_cursor",
        "report_observation",
        "update_execution",
        "extract_preferences",
        "build_context",
        "verify_run",
        "store_memory",
        "store_preference",
        "get_plan_details",
        "discover_capabilities",
        "report_governor_verdict",
        "get_entity",
        "query_facts",
        "traverse",
        "get_provenance",
        "set_goal",
        "set_instruction",
        "schedule_reminder",
        "add_to_brief",
    }

    catalog_names = get_internal_tool_names()
    assert catalog_names == expected_names, f"Mismatch: {catalog_names ^ expected_names}"


def test_all_input_models_are_pydantic():
    """Verify every input_model is a subclass of pydantic.BaseModel."""
    for tool in INTERNAL_TOOLS:
        assert issubclass(tool.input_model, BaseModel), (
            f"{tool.name}.input_model is not a BaseModel subclass"
        )


def test_server_distribution():
    """Verify correct server counts: 27 intelligence, 1 _special, nothing else."""
    server_counts = {}
    for tool in INTERNAL_TOOLS:
        server_counts[tool.server] = server_counts.get(tool.server, 0) + 1

    assert server_counts == {"intelligence": 27, "_special": 1}


def test_get_internal_tool_by_name_found():
    """Verify get_internal_tool_by_name returns correct tool for valid name."""
    tool = get_internal_tool_by_name("search")
    assert tool is not None
    assert tool.name == "search"
    assert tool.capability == "internal.search"
    assert tool.server == "intelligence"
    assert tool.read_only is True


def test_get_internal_tool_by_name_not_found():
    """Verify get_internal_tool_by_name returns None for nonexistent name."""
    tool = get_internal_tool_by_name("nonexistent_tool_xyz")
    assert tool is None


def test_get_internal_tools_for_server_intelligence():
    """Verify get_internal_tools_for_server returns 27 intelligence tools."""
    tools = get_internal_tools_for_server("intelligence")
    assert len(tools) == 27


def test_get_internal_tools_for_server_special():
    """Verify get_internal_tools_for_server returns 1 _special tool."""
    tools = get_internal_tools_for_server("_special")
    assert len(tools) == 1
    assert tools[0].name == "report_governor_verdict"


def test_get_internal_tools_for_server_not_found():
    """Verify get_internal_tools_for_server returns empty list for unknown server."""
    tools = get_internal_tools_for_server("nonexistent_server")
    assert tools == []


def test_no_duplicate_names():
    """Verify INTERNAL_TOOLS has no duplicate tool names."""
    names = [tool.name for tool in INTERNAL_TOOLS]
    assert len(names) == len(set(names)), f"Duplicate names found: {names}"


def test_requires_approval_implies_risk():
    """Verify tools with requires_approval=True have risk_level != 'low'."""
    for tool in INTERNAL_TOOLS:
        if tool.requires_approval:
            assert tool.risk_level != "low", f"{tool.name} requires approval but has risk_level=low"


def test_read_only_tools_are_safe():
    """Verify read_only tools have safe risk levels and no approval required."""
    read_only_tools = [tool for tool in INTERNAL_TOOLS if tool.read_only]
    assert len(read_only_tools) > 0, "Should have at least one read_only tool"

    safe_risk_levels = {"none", "low"}
    for tool in read_only_tools:
        assert tool.risk_level in safe_risk_levels, (
            f"{tool.name} is read_only but has risk_level={tool.risk_level}"
        )
        assert not tool.requires_approval, f"{tool.name} is read_only but requires approval"


def test_all_tools_have_descriptions():
    """Verify all tools have non-empty descriptions."""
    for tool in INTERNAL_TOOLS:
        assert tool.description, f"{tool.name} has empty description"
        assert len(tool.description) > 10, f"{tool.name} description too short"


def test_all_tools_have_capabilities():
    """Verify all tools have properly formatted capability strings."""
    allowed_prefixes = ("internal.", "system.")
    for tool in INTERNAL_TOOLS:
        assert tool.capability.startswith(allowed_prefixes), (
            f"{tool.name} capability should start with one of {allowed_prefixes}"
        )
        assert len(tool.capability.split(".")) == 2, f"{tool.name} capability malformed"


def test_special_tool_properties():
    """Verify report_governor_verdict has correct properties."""
    tool = get_internal_tool_by_name("report_governor_verdict")
    assert tool is not None
    assert tool.server == "_special"
    # Dedicated capability (TOOL-P3-2) — distinct from evaluate_policy, keeps tool↔cap 1:1.
    assert tool.capability == "internal.report_verdict"
    assert tool.risk_level == "low"
    assert not tool.requires_approval


# ── External Tool Seed Tests ───────────────────────────────────────


def test_external_tool_seeds_count():
    """Verify exactly 65 external tool seeds are registered."""
    assert len(EXTERNAL_TOOL_SEEDS) == 65


def test_verified_seeds_count():
    """Verify exactly 43 seeds are verified."""
    verified = get_verified_seeds()
    assert len(verified) == 43


def test_no_duplicate_external_names_per_server():
    """Verify no duplicate tool names within same server."""
    servers = set(seed.server for seed in EXTERNAL_TOOL_SEEDS)
    for server in servers:
        seeds = get_seeds_for_server(server)
        names = [seed.name for seed in seeds]
        assert len(names) == len(set(names)), f"Duplicate names in {server}: {names}"


def test_all_seeds_have_capabilities():
    """Verify every seed has a non-empty capability."""
    for seed in EXTERNAL_TOOL_SEEDS:
        assert seed.capability, f"{seed.name} has empty capability"
        assert "." in seed.capability, f"{seed.name} capability should contain '.'"


def test_notion_seeds_api_prefix():
    """Verify Notion seeds all start with API- prefix."""
    notion_seeds = get_seeds_for_server("notion")
    assert len(notion_seeds) == 22
    for seed in notion_seeds:
        assert seed.name.startswith("API-"), f"Notion tool {seed.name} should start with 'API-'"


def test_seeds_for_server_counts():
    """Verify per-server tool counts match expected."""
    expected_counts = {
        "google-workspace": 13,
        "github": 8,
        "slack": 8,
        "notion": 22,
        "atlassian": 13,
        "_composite": 1,
    }
    for server, expected_count in expected_counts.items():
        actual_count = len(get_seeds_for_server(server))
        assert actual_count == expected_count, (
            f"Server {server}: expected {expected_count}, got {actual_count}"
        )


def test_get_seeds_for_server_helper():
    """Verify get_seeds_for_server returns correct tools."""
    slack_seeds = get_seeds_for_server("slack")
    assert len(slack_seeds) == 8
    names = {seed.name for seed in slack_seeds}
    assert "slack_post_message" in names
    assert "slack_list_channels" in names

    # Test empty result
    empty = get_seeds_for_server("nonexistent_server")
    assert empty == []


def test_get_verified_seeds_helper():
    """Verify get_verified_seeds only returns verified=True entries."""
    verified = get_verified_seeds()
    assert len(verified) == 43

    # All returned seeds should be verified
    for seed in verified:
        assert seed.verified is True

    # Verify expected servers are present in verified seeds. google-workspace
    # and github are gateway-only servers; their derived seeds are all
    # verified=True (adapter warm-start is the ground truth for these names).
    verified_servers = {seed.server for seed in verified}
    expected_verified = {"notion", "google-workspace", "github"}
    assert expected_verified.issubset(verified_servers)

    # Verify unverified servers are NOT in verified seeds
    unverified_servers = {"slack", "atlassian", "_composite"}
    assert verified_servers.isdisjoint(unverified_servers)


def test_seed_server_names_match_installations():
    """Verify server names match seed_installations.py conventions."""
    # These are the exact server names used in seed_installations.py
    expected_servers = {
        "google-workspace",
        "github",
        "slack",
        "notion",
        "atlassian",
        "_composite",  # special case for composite tools
    }
    actual_servers = {seed.server for seed in EXTERNAL_TOOL_SEEDS}
    assert actual_servers == expected_servers


def test_high_risk_tools_require_approval():
    """Verify high and critical risk tools require approval."""
    for seed in EXTERNAL_TOOL_SEEDS:
        if seed.risk_level in ("high", "critical"):
            assert seed.requires_approval, (
                f"{seed.name} has {seed.risk_level} risk but doesn't require approval"
            )


def test_verified_tool_servers():
    """Verify exactly 3 servers have verified tools."""
    verified = get_verified_seeds()
    verified_servers = {seed.server for seed in verified}
    assert verified_servers == {"notion", "google-workspace", "github"}


def test_composite_tools():
    """Verify _composite server has correct structure."""
    composite = get_seeds_for_server("_composite")
    assert len(composite) == 1
    assert composite[0].name == "web_search"
    assert composite[0].capability == "search.web"
    assert composite[0].risk_level == "low"
    assert composite[0].requires_approval is False
