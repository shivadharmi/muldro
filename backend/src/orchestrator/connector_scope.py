"""Connector-derived capability scope for the planless chat lead (P2.5b).

Where ``lead_builder.derive_lead_scope`` derives a PLAN-bounded scope from a PlanOutput's
steps (the flag-off / autonomous path), ``resolve_connector_scope`` derives a
CONNECTOR-bounded scope for the (future, P2.5c) planless lead — the lead that no longer has a
plan to bound it:

    scope = INTERNAL_READ_FLOOR  ∪  SYSTEM_ACTION_CAPABILITIES  ∪  authenticated-connector caps

- ``INTERNAL_READ_FLOOR`` — internal READ capabilities that need no external connector (the
  lead's always-available knowledge / world-model / context surface).
- ``SYSTEM_ACTION_CAPABILITIES`` — the 4 promoted system.* internal writes (P2.5a): the user's
  own goals / instructions / reminders / briefing, ALWAYS available (they gate/lock-exempt).
- Connector caps — for each ACTIVE + HEALTHY connector the user has authenticated, the union of
  its server's tool capabilities.

Enforced downstream by the existing ``capability_scope`` middleware (fail-closed): the lead can
only call in-scope tools, and connector WRITES remain gated by ``permission_gate`` (system.*
are exempt, D5). DORMANT: nothing calls this until P2.5c wires the planless lead.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.capabilities import SYSTEM_ACTION_CAPABILITIES
from src.services.integration_manager import IntegrationManager
from src.services.tool_registry import ToolRegistry

# Internal READ capabilities available to the lead regardless of connectors — its knowledge /
# world-model / context / goals surface. Curated to LEAST AUTHORITY: the read-only internal caps
# a chat lead uses to answer the user and reference their world, plus ``system.discovery``. It is
# every read_only internal cap EXCEPT ``internal.get_cursor`` (a perception observation-cursor
# read with no chat-lead meaning). Note ``internal.evaluate_policy`` / ``internal.verify_run`` are
# NOT reads at all (read_only=False writes) so they were never floor candidates. ``knowledge.*`` is
# a routing-only virtual capability with NO backing tool, so it is NOT here (it would grant zero
# authority). All members are read_only=True internal caps (asserted in tests).
INTERNAL_READ_FLOOR: frozenset[str] = frozenset(
    {
        "internal.search",  # memory / semantic search
        "internal.query_facts",  # world-model fact query
        "internal.traverse",  # world-model graph traversal
        "internal.get_entity",  # world-model entity read
        "internal.get_provenance",  # world-model provenance read
        "internal.build_context",  # context-pack assembly
        "internal.get_briefing",  # briefing read
        "internal.get_goals",  # user goal-memories read
        "internal.get_plans",  # active plans read
        "internal.get_plan_details",  # plan detail read
        "system.discovery",  # the discover_capabilities tool
    }
)


async def resolve_connector_scope(
    user_id: str, workspace_id: str, db: AsyncSession
) -> frozenset[str]:
    """Compute the planless lead's ``capability_scope`` from authenticated connectors.

    ``= INTERNAL_READ_FLOOR ∪ SYSTEM_ACTION_CAPABILITIES ∪ {caps of each active+healthy
    connector's server}``. Only connectors with ``status == "active"`` AND
    ``health_status == "healthy"`` contribute (fail-closed — an unknown/degraded connector's
    tools may be broken, so they are not offered). ``provider`` (the installation's
    ``server_name``) maps 1:1 to a ``ToolRegistry`` server (``connector_type``), so it is passed
    through directly; a provider that matches no tools simply contributes nothing (skip-unmatched,
    fail-closed). Tools with no mapped capability (``capability is None``) are dropped.

    Fails LOUD on an empty ``user_id`` / ``workspace_id`` — a blank ``workspace_id`` would make
    ``get_user_connectors`` drop its workspace filter and return the user's connectors across ALL
    workspaces (cross-workspace leak). This is a security-boundary function; the caller must scope
    it explicitly.
    """
    if not user_id or not workspace_id:
        raise ValueError("resolve_connector_scope requires non-empty user_id and workspace_id")

    scope: set[str] = set(INTERNAL_READ_FLOOR) | set(SYSTEM_ACTION_CAPABILITIES)

    manager = IntegrationManager(db)
    registry = ToolRegistry(db, workspace_id)

    connectors = await manager.get_user_connectors(user_id, workspace_id)
    for connector in connectors:
        if connector.get("status") != "active" or connector.get("health_status") != "healthy":
            continue
        server = connector.get("provider")
        if not server:
            continue
        tools = await registry.list_tools(connector_type=server, enabled_only=True)
        # Tenant guard: ToolRegistry.list_tools is NOT workspace-scoped (it filters only on
        # connector_type + enabled — a shared-method gap that also affects tool_executor /
        # step_runner, tracked separately as a broader fix). Filter here so a workspace-specific
        # ToolDefinition belonging to ANOTHER tenant can never grant a capability into this
        # workspace's scope. Global tools (workspace_id IS NULL, the seeded connector schemas) and
        # this workspace's own rows are kept; other workspaces' rows are dropped.
        scope |= {
            tool.capability
            for tool in tools
            if tool.capability and getattr(tool, "workspace_id", None) in (None, workspace_id)
        }

    return frozenset(scope)
