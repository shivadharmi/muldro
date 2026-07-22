"""P2.5a: ``system.*`` action capabilities promoted to internal MCP tools.

The 4 write tools (``set_goal`` / ``set_instruction`` / ``schedule_reminder`` /
``add_to_brief``) are additive wrappers that call the SAME service methods
``SystemCapabilityHandler`` already uses — no business-logic duplication. These contract
tests prove each tool hits the right service call, and that the catalog/schema/registry
seams accept the 4 new tools (so seed-on-restart + ``validate_registry`` stay green).

Purely additive: ``SystemCapabilityHandler`` is untouched (its own tests stay green) and no
agent scope grants these caps yet — they are surfaced only once P2.5c wires the planless lead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools import intelligence_server


def _mock_ctx():
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.error = AsyncMock()
    ctx.warning = AsyncMock()
    return ctx


@pytest.fixture
def configured():
    """Configure the intelligence server with a mock db_factory + services container.

    Mirrors ``tests/test_intelligence_tools_contracts.py``: ``ctx["services"]`` is the
    container ``_shared.request_services(db)`` returns, so setting ``.memory_service`` on it
    flows straight into the tool impls.
    """
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    async_cm = AsyncMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_session)
    async_cm.__aexit__ = AsyncMock(return_value=False)
    mock_db_factory = MagicMock(return_value=async_cm)

    mock_services = MagicMock()

    intelligence_server.configure(mock_db_factory, MagicMock(), mock_services)
    try:
        yield {"session": mock_session, "services": mock_services}
    finally:
        intelligence_server._db_factory = None
        intelligence_server._settings = None
        intelligence_server._services = None


# ── tool impls: same service calls as the handler ────────────────────────────


async def test_set_goal_calls_store_goal_memory(configured):
    memory_svc = MagicMock()
    memory_svc.store_goal_memory = AsyncMock(return_value="mem_goal")
    configured["services"].memory_service = memory_svc

    result = await intelligence_server.set_goal(
        title="Ship P2.5", ctx=_mock_ctx(), priority="high", user_id="usr_1", workspace_id="ws_1"
    )

    memory_svc.store_goal_memory.assert_awaited_once_with(
        user_id="usr_1", workspace_id="ws_1", title="Ship P2.5", priority="high"
    )
    configured["session"].commit.assert_awaited()
    assert result["status"] == "created"
    assert result["memory_id"] == "mem_goal"


async def test_set_instruction_calls_store_instruction_memory(configured):
    memory_svc = MagicMock()
    memory_svc.store_instruction_memory = AsyncMock(return_value="mem_inst")
    configured["services"].memory_service = memory_svc

    result = await intelligence_server.set_instruction(
        instruction_text="Always CC finance",
        ctx=_mock_ctx(),
        instruction_type="preference",
        user_id="usr_1",
        workspace_id="ws_1",
    )

    memory_svc.store_instruction_memory.assert_awaited_once_with(
        user_id="usr_1",
        workspace_id="ws_1",
        instruction_text="Always CC finance",
        instruction_type="preference",
    )
    configured["session"].commit.assert_awaited()
    assert result["status"] == "created"
    assert result["memory_id"] == "mem_inst"


async def test_add_to_brief_calls_store_briefing_memory(configured):
    memory_svc = MagicMock()
    memory_svc.store_briefing_memory = AsyncMock(return_value="mem_brief")
    configured["services"].memory_service = memory_svc

    result = await intelligence_server.add_to_brief(
        text="Board call Friday", ctx=_mock_ctx(), user_id="usr_1", workspace_id="ws_1"
    )

    memory_svc.store_briefing_memory.assert_awaited_once_with(
        user_id="usr_1", workspace_id="ws_1", text="Board call Friday"
    )
    configured["session"].commit.assert_awaited()
    assert result["status"] == "stored"
    assert result["memory_id"] == "mem_brief"


async def test_schedule_reminder_adds_one_shot_schedule(configured):
    from src.models.schedules import Schedule

    result = await intelligence_server.schedule_reminder(
        title="Renew domain", ctx=_mock_ctx(), user_id="usr_1", workspace_id="ws_1"
    )

    # A one-shot Schedule row was staged + committed (mirrors the handler).
    configured["session"].add.assert_called_once()
    (added,), _ = configured["session"].add.call_args
    assert isinstance(added, Schedule)
    assert added.schedule_type == "one_shot"
    assert added.user_id == "usr_1"
    assert added.workspace_id == "ws_1"
    assert added.action_type == "custom_agent_task"
    assert "Renew domain" in added.action_config["instructions"]
    configured["session"].commit.assert_awaited()
    assert result["status"] == "created"
    assert result["schedule_id"].startswith("sched_")


async def test_schedule_reminder_run_at_sets_next_run_at(configured):
    from datetime import datetime, timezone

    from src.models.schedules import Schedule

    result = await intelligence_server.schedule_reminder(
        title="Call John",
        ctx=_mock_ctx(),
        run_at="2026-07-23T15:00:00Z",
        user_id="usr_1",
        workspace_id="ws_1",
    )

    (added,), _ = configured["session"].add.call_args
    assert isinstance(added, Schedule)
    want = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
    assert added.run_at == want
    assert added.next_run_at == want
    assert result["status"] == "created"


async def test_schedule_reminder_rejects_natural_language_run_at(configured):
    result = await intelligence_server.schedule_reminder(
        title="Call John",
        ctx=_mock_ctx(),
        run_at="tomorrow at 3pm",
        user_id="usr_1",
        workspace_id="ws_1",
    )
    # Rejected at model validation — nothing persisted.
    assert result["status"] == "error"
    configured["session"].add.assert_not_called()


async def test_memory_service_unavailable_returns_error(configured):
    """When the container has no memory_service, the tool returns a clean error dict rather
    than raising (fail-soft, mirrors the handler's guard)."""
    configured["services"].memory_service = None

    result = await intelligence_server.set_goal(
        title="x", ctx=_mock_ctx(), user_id="usr_1", workspace_id="ws_1"
    )
    assert result["status"] == "error"


# ── catalog / schema / registry seams accept the 4 new tools ─────────────────


def test_four_system_caps_are_catalogued():
    from src.integrations.capabilities import CAPABILITY_CATALOG, CapabilityFamily

    for cap in (
        "system.set_goal",
        "system.set_instruction",
        "system.schedule_reminder",
        "system.add_to_brief",
    ):
        assert cap in CAPABILITY_CATALOG, f"{cap} must be catalogued"
        meta = CAPABILITY_CATALOG[cap]
        assert meta.family == CapabilityFamily.SYSTEM
        assert meta.read_only is False  # they are writes (so gate/lock see them as writes)


def test_four_tools_registered_with_input_models():
    from src.tools.catalog import INTERNAL_TOOLS
    from src.tools.schemas import TOOL_INPUT_MODELS

    by_name = {t.name: t for t in INTERNAL_TOOLS}
    for name, cap in (
        ("set_goal", "system.set_goal"),
        ("set_instruction", "system.set_instruction"),
        ("schedule_reminder", "system.schedule_reminder"),
        ("add_to_brief", "system.add_to_brief"),
    ):
        assert name in by_name, f"{name} must be in INTERNAL_TOOLS"
        tool = by_name[name]
        assert tool.capability == cap
        assert tool.server == "intelligence"
        assert tool.read_only is False
        assert tool.requires_approval is False
        assert name in TOOL_INPUT_MODELS, f"{name} must have an input model (validate_registry)"


def test_validate_registry_still_passes_with_new_tools():
    """The 4 additions must not trip any startup cross-check."""
    from src.tools.validation import validate_registry

    assert validate_registry() == []


def test_system_caps_classified_reversible_and_covered():
    """The 4 system.* writes are reversible-internal (self blast-radius, undoable) — NOT
    irreversible — so the startup post-condition coverage gate (which fails closed on any
    unregistered irreversible write) accepts them without a read-back post-condition. This
    is what makes the app boot with the new caps present (regression guard)."""
    from src.integrations.capabilities import SYSTEM_ACTION_CAPABILITIES
    from src.services.verification.post_conditions import validate_post_condition_coverage
    from src.services.verification.predicate import (
        is_irreversible_capability,
        write_capabilities,
    )

    for cap in SYSTEM_ACTION_CAPABILITIES:
        assert not is_irreversible_capability(cap), f"{cap} must be reversible-internal"

    # Full coverage gate (the exact check app startup runs) must be clean.
    assert validate_post_condition_coverage(write_capabilities()) == []


def test_middleware_exemption_is_explicit_set_not_prefix():
    """The always-allowed set is exactly the 4 promoted caps — a hypothetical future
    ``system.*`` capability is NOT auto-exempt (safe-by-construction, Security I1)."""
    from src.integrations.capabilities import SYSTEM_ACTION_CAPABILITIES

    assert SYSTEM_ACTION_CAPABILITIES == frozenset(
        {
            "system.set_goal",
            "system.set_instruction",
            "system.schedule_reminder",
            "system.add_to_brief",
        }
    )
    # A read/meta system cap and an invented future one are NOT in the write-exemption set.
    assert "system.discovery" not in SYSTEM_ACTION_CAPABILITIES
    assert "system.delete_everything" not in SYSTEM_ACTION_CAPABILITIES
