"""Intelligence MCP server — wraps existing Jarvis services as MCP tools.

Built with FastMCP. Provides tools for event ingestion, memory search, entity
management, planning, policy evaluation, briefings, cursors, and approvals.
These are the internal tools that Jarvis sub-agents use to interact with the
intelligence layer.

Split by domain (TOOL-P2-4): the FastMCP ``intelligence`` instance, runtime
``configure()``, and the ``_get_db`` helper live in ``_shared``; tool
implementations live in ``observation``, ``memory``, ``planning`` and
``persona``. This package ``__init__`` is the public facade — importing it
registers every tool/resource on ``intelligence`` and re-exports the prior
module's public names, so existing import paths
(``from src.tools.intelligence_server import intelligence / configure /
approve_action / _get_plan_details_impl``) are unchanged.
"""

from src.tools.intelligence_server._shared import _get_db, configure, intelligence

# Importing the domain modules runs their @intelligence.tool / .resource
# decorators, registering every tool on the shared `intelligence` instance.
from src.tools.intelligence_server.memory import (
    build_context,
    get_goal_memories,
    recent_entities_resource,
    search,
    store_memory,
    update_entity,
)
from src.tools.intelligence_server.observation import (
    get_observation_cursor,
    ingest_event,
    report_observation,
    update_observation_cursor,
)
from src.tools.intelligence_server.persona import (
    extract_preferences,
    get_briefing,
    store_preference,
)
from src.tools.intelligence_server.planning import (
    _get_plan_details_impl,
    active_plans_resource,
    approve_action,
    discover_capabilities,
    evaluate_policy,
    get_active_plans,
    get_plan_details,
    update_execution,
    verify_run,
)
from src.tools.intelligence_server.world_model_tools import (
    get_entity,
    get_provenance,
    query_facts,
    traverse,
)

__all__ = [
    "intelligence",
    "configure",
    "_get_db",
    "_get_plan_details_impl",
    "ingest_event",
    "get_observation_cursor",
    "update_observation_cursor",
    "report_observation",
    "search",
    "update_entity",
    "get_goal_memories",
    "build_context",
    "store_memory",
    "recent_entities_resource",
    "get_plan_details",
    "get_active_plans",
    "evaluate_policy",
    "approve_action",
    "update_execution",
    "verify_run",
    "active_plans_resource",
    "discover_capabilities",
    "extract_preferences",
    "get_briefing",
    "store_preference",
    "get_entity",
    "query_facts",
    "traverse",
    "get_provenance",
]
