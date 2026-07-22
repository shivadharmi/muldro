"""P2.5b: ``resolve_connector_scope`` — the planless chat lead's capability_scope source.

Where ``derive_lead_scope`` (lead_builder) derives a PLAN-bounded scope from a PlanOutput's
steps, ``resolve_connector_scope`` derives a CONNECTOR-bounded scope for the (future, P2.5c)
planless lead that has no plan:

    scope = INTERNAL_READ_FLOOR ∪ SYSTEM_ACTION_CAPABILITIES ∪ authenticated-connector caps

Negative-controls have TEETH: an inactive/unhealthy connector contributes NO caps; the
internal read floor + system.* write caps are ALWAYS present; a tool with capability=None is
dropped. DORMANT: nothing calls this until P2.5c.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.integrations.capabilities import SYSTEM_ACTION_CAPABILITIES
from src.orchestrator.connector_scope import (
    INTERNAL_READ_FLOOR,
    resolve_connector_scope,
)

MODULE = "src.orchestrator.connector_scope"


def _connector(provider: str, *, status: str = "active", health: str = "healthy") -> dict:
    """Mirror get_user_connectors' return dict shape (only the fields the fn reads)."""
    return {"provider": provider, "status": status, "health_status": health}


def _tool(capability, *, workspace_id=None):
    """A ToolDefinition-like with the .capability + .workspace_id the fn reads.

    workspace_id defaults to None (a global/seeded connector tool — the common case).
    """
    return SimpleNamespace(capability=capability, workspace_id=workspace_id)


def _patched(*, connectors: list[dict], tools_by_server: dict[str, list]):
    """Patch IntegrationManager + ToolRegistry in the connector_scope namespace.

    ``get_user_connectors`` returns *connectors*; ``list_tools(connector_type=...)`` returns
    ``tools_by_server[connector_type]`` (empty for an unmatched server — the fail-closed
    skip-unmatched path). The mock SIMULATES the real ``list_tools`` SQL tenant bound: when
    ``workspace_scoped=True`` (what ``resolve_connector_scope`` passes) it drops rows whose
    ``workspace_id`` is neither NULL (global) nor the turn's ``ws_1``.
    """
    mgr = SimpleNamespace(get_user_connectors=AsyncMock(return_value=connectors))

    async def _list_tools(connector_type=None, enabled_only=True, workspace_scoped=False):
        tools = list(tools_by_server.get(connector_type, []))
        if workspace_scoped:
            tools = [t for t in tools if getattr(t, "workspace_id", None) in (None, "ws_1")]
        return tools

    registry = SimpleNamespace(list_tools=AsyncMock(side_effect=_list_tools))
    return (
        patch(f"{MODULE}.IntegrationManager", return_value=mgr),
        patch(f"{MODULE}.ToolRegistry", return_value=registry),
        registry,
    )


async def _run(connectors, tools_by_server):
    p_mgr, p_reg, registry = _patched(connectors=connectors, tools_by_server=tools_by_server)
    with p_mgr, p_reg:
        scope = await resolve_connector_scope("usr_1", "ws_1", db=object())
    return scope, registry


# ── floor + system caps are ALWAYS present ───────────────────────────────────


async def test_no_connectors_yields_floor_plus_system_caps_only():
    scope, _ = await _run(connectors=[], tools_by_server={})

    assert scope == INTERNAL_READ_FLOOR | SYSTEM_ACTION_CAPABILITIES
    # Teeth: no external write capability is present without a connector.
    assert "email.send" not in scope
    assert "repo.create_pr" not in scope


async def test_floor_and_system_caps_present_even_with_connectors():
    scope, _ = await _run(
        connectors=[_connector("github")],
        tools_by_server={"github": [_tool("repo.create_pr"), _tool("repo.search_code")]},
    )
    # The floor + system caps survive alongside connector caps.
    assert INTERNAL_READ_FLOOR <= scope
    assert SYSTEM_ACTION_CAPABILITIES <= scope
    assert {"repo.create_pr", "repo.search_code"} <= scope


# ── authenticated-connector caps flow in ─────────────────────────────────────


async def test_active_healthy_connector_adds_its_server_caps():
    scope, registry = await _run(
        connectors=[_connector("google-workspace")],
        tools_by_server={"google-workspace": [_tool("email.send"), _tool("calendar.create")]},
    )
    assert {"email.send", "calendar.create"} <= scope
    # provider is passed through as connector_type identity (Q1: 1:1, no lookup table), and the
    # tenant bound is delegated to list_tools' SQL scoping (workspace_scoped=True).
    registry.list_tools.assert_awaited_once_with(
        connector_type="google-workspace", enabled_only=True, workspace_scoped=True
    )


async def test_multiple_connectors_union_their_caps():
    scope, _ = await _run(
        connectors=[_connector("github"), _connector("slack")],
        tools_by_server={
            "github": [_tool("repo.create_pr")],
            "slack": [_tool("chat.post_message")],
        },
    )
    assert {"repo.create_pr", "chat.post_message"} <= scope


# ── inactive / unhealthy connectors are excluded (fail-closed) ───────────────


async def test_inactive_connector_contributes_nothing():
    scope, registry = await _run(
        connectors=[_connector("github", status="paused")],
        tools_by_server={"github": [_tool("repo.create_pr")]},
    )
    assert "repo.create_pr" not in scope
    # An excluded connector is never even queried for its tools.
    registry.list_tools.assert_not_awaited()
    # Floor + system caps still present.
    assert scope == INTERNAL_READ_FLOOR | SYSTEM_ACTION_CAPABILITIES


async def test_unhealthy_connector_contributes_nothing():
    scope, registry = await _run(
        connectors=[_connector("github", health="degraded")],
        tools_by_server={"github": [_tool("repo.create_pr")]},
    )
    assert "repo.create_pr" not in scope
    registry.list_tools.assert_not_awaited()


async def test_unknown_health_connector_is_excluded():
    """A freshly-installed connector defaults to health_status='unknown' — excluded until a
    health tick marks it healthy (fail-closed: only proven-healthy connectors grant tools)."""
    scope, _ = await _run(
        connectors=[_connector("slack", health="unknown")],
        tools_by_server={"slack": [_tool("chat.post_message")]},
    )
    assert "chat.post_message" not in scope


# ── tool with no capability is dropped ───────────────────────────────────────


async def test_uncapped_tool_is_dropped():
    """An auto-registered MCP tool with capability=None (not yet capability-mapped) grants no
    authority — filtered out rather than adding a `None` to the scope set."""
    scope, _ = await _run(
        connectors=[_connector("notion")],
        tools_by_server={"notion": [_tool(None), _tool("doc.create_page")]},
    )
    assert None not in scope
    assert "doc.create_page" in scope


async def test_unmatched_provider_skips_cleanly():
    """A connector whose provider matches no tools (unmatched server) contributes nothing —
    fail-closed skip, no crash."""
    scope, _ = await _run(
        connectors=[_connector("some_future_provider")],
        tools_by_server={},  # list_tools returns [] for it
    )
    assert scope == INTERNAL_READ_FLOOR | SYSTEM_ACTION_CAPABILITIES


# ── tenant isolation: another workspace's tool is dropped ────────────────────


async def test_other_workspace_tool_is_dropped():
    """ToolRegistry.list_tools is not workspace-scoped, so it can return a workspace-specific
    ToolDefinition belonging to ANOTHER tenant. resolve_connector_scope must drop it: only global
    (workspace_id=None) and this-workspace rows may grant caps (cross-tenant guard)."""
    scope, _ = await _run(
        connectors=[_connector("slack")],
        tools_by_server={
            "slack": [
                _tool("chat.global", workspace_id=None),  # global seed → kept
                _tool("chat.mine", workspace_id="ws_1"),  # this workspace → kept
                _tool("chat.other_tenant", workspace_id="ws_OTHER"),  # foreign → DROPPED
            ]
        },
    )
    assert "chat.global" in scope
    assert "chat.mine" in scope
    assert "chat.other_tenant" not in scope


# ── fail-loud on missing scope args (security boundary) ──────────────────────


async def test_empty_workspace_id_raises():
    p_mgr, p_reg, _ = _patched(connectors=[], tools_by_server={})
    with p_mgr, p_reg:
        try:
            await resolve_connector_scope("usr_1", "", db=object())
            raise AssertionError("expected ValueError on empty workspace_id")
        except ValueError:
            pass


async def test_empty_user_id_raises():
    p_mgr, p_reg, _ = _patched(connectors=[], tools_by_server={})
    with p_mgr, p_reg:
        try:
            await resolve_connector_scope("", "ws_1", db=object())
            raise AssertionError("expected ValueError on empty user_id")
        except ValueError:
            pass


# ── the floor itself ─────────────────────────────────────────────────────────


def test_floor_is_read_only_internal_plus_discovery():
    """INTERNAL_READ_FLOOR is exactly the curated internal READ capabilities the lead needs
    (knowledge/world-model/context/goals reads + discovery). It carries NO write capability and
    NO connector cap — those come from the other two scope sources."""
    from src.integrations.capabilities import CAPABILITY_CATALOG, is_read_only_capability

    for cap in INTERNAL_READ_FLOOR:
        assert cap in CAPABILITY_CATALOG, f"{cap} must be catalogued"
        assert is_read_only_capability(cap), f"{cap} in the read floor must be read-only"
    # Teeth: no write caps leaked into the floor.
    assert "internal.store_memory" not in INTERNAL_READ_FLOOR
    assert not (INTERNAL_READ_FLOOR & SYSTEM_ACTION_CAPABILITIES)  # floor is reads, not sys writes
