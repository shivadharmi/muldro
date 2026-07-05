"""Characterization test pinning the intelligence MCP server's registered surface.

Safety net for the domain-package split (TOOL-P2-4): importing
`src.tools.intelligence_server` must register the exact same set of tools and
resource templates on the shared `intelligence` FastMCP instance, regardless of
which submodule each tool now lives in. A dropped @intelligence.tool decorator
(e.g. a submodule not imported by the facade) would silently remove a tool —
this test turns that into a hard failure.
"""

import pytest

from src.tools.intelligence_server import intelligence

EXPECTED_TOOLS = {
    "approve_action",
    "build_context",
    "discover_capabilities",
    "evaluate_policy",
    "extract_preferences",
    "get_active_plans",
    "get_briefing",
    "get_entity",
    "get_goal_memories",
    "get_observation_cursor",
    "get_plan_details",
    "get_provenance",
    "ingest_event",
    "query_facts",
    "report_observation",
    "search",
    "store_memory",
    "store_preference",
    "traverse",
    "update_entity",
    "update_execution",
    "update_observation_cursor",
    "verify_run",
}

EXPECTED_RESOURCE_TEMPLATES = {
    "active_plans_resource",
    "recent_entities_resource",
}


@pytest.mark.asyncio
async def test_registered_tools_exact_set():
    tools = await intelligence.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    assert len(tools) == len(EXPECTED_TOOLS)


@pytest.mark.asyncio
async def test_registered_resource_templates_exact_set():
    templates = await intelligence.list_resource_templates()
    names = {t.name for t in templates}
    assert names == EXPECTED_RESOURCE_TEMPLATES


def test_public_exports_present():
    """Import paths relied on by external consumers must keep resolving."""
    import src.tools.intelligence_server as srv

    # configure() + the FastMCP instance (run.py, server.py, prompts.py)
    assert callable(srv.configure)
    assert srv.intelligence is intelligence
    # approve_action (impl in intelligence_server/planning.py) + _get_plan_details_impl (tests)
    assert callable(srv.approve_action)
    assert callable(srv._get_plan_details_impl)
