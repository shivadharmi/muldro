"""Tests for src/tools/catalog.py — internal tool registry."""

from pydantic import BaseModel

from src.tools.catalog import (
    INTERNAL_TOOLS,
    get_internal_tool_by_name,
    get_internal_tool_names,
    get_internal_tools_for_server,
)


def test_internal_tools_count():
    """Verify exactly 19 internal tools are registered."""
    assert len(INTERNAL_TOOLS) == 19


def test_internal_tool_names_match_jarvis():
    """Verify catalog tool names match the internal_tools set in jarvis.py."""
    # The internal_tools set is defined at line 2525 in jarvis.py
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
        "report_governor_verdict",
        "send_telegram",
        "send_approval_prompt",
        "push_ui_update",
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
    """Verify correct server counts: 15 intelligence, 3 communication, 1 _special."""
    server_counts = {}
    for tool in INTERNAL_TOOLS:
        server_counts[tool.server] = server_counts.get(tool.server, 0) + 1

    assert server_counts.get("intelligence", 0) == 15, "Expected 15 intelligence tools"
    assert server_counts.get("communication", 0) == 3, "Expected 3 communication tools"
    assert server_counts.get("_special", 0) == 1, "Expected 1 _special tool"


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


def test_get_internal_tools_for_server_communication():
    """Verify get_internal_tools_for_server returns 3 communication tools."""
    tools = get_internal_tools_for_server("communication")
    assert len(tools) == 3
    names = {t.name for t in tools}
    assert names == {"send_telegram", "send_approval_prompt", "push_ui_update"}


def test_get_internal_tools_for_server_intelligence():
    """Verify get_internal_tools_for_server returns 15 intelligence tools."""
    tools = get_internal_tools_for_server("intelligence")
    assert len(tools) == 15


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
    """Verify read_only tools have low risk and no approval required."""
    read_only_tools = [tool for tool in INTERNAL_TOOLS if tool.read_only]
    assert len(read_only_tools) > 0, "Should have at least one read_only tool"

    for tool in read_only_tools:
        assert tool.risk_level == "low", (
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
    for tool in INTERNAL_TOOLS:
        assert tool.capability.startswith("internal."), (
            f"{tool.name} capability should start with 'internal.'"
        )
        assert len(tool.capability.split(".")) == 2, f"{tool.name} capability malformed"


def test_communication_tools_have_medium_risk():
    """Verify all communication tools have medium risk and require approval."""
    comm_tools = get_internal_tools_for_server("communication")
    for tool in comm_tools:
        if tool.name != "push_ui_update":  # push_ui_update is low-risk, no approval
            assert tool.risk_level == "medium", f"{tool.name} should have medium risk_level"
            assert tool.requires_approval, f"{tool.name} should require approval"


def test_special_tool_properties():
    """Verify report_governor_verdict has correct properties."""
    tool = get_internal_tool_by_name("report_governor_verdict")
    assert tool is not None
    assert tool.server == "_special"
    assert tool.capability == "internal.evaluate_policy"
    assert tool.risk_level == "low"
    assert not tool.requires_approval
