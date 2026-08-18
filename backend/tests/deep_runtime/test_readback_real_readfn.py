"""B4/A2: the REAL deep-runtime read-back read_fn (``make_readback_read_fn``).

Step 7C wired the inline read-back middleware with ``read_fn=None`` (every irreversible
write resolved UNVERIFIED, never CONTRADICTED — 10A locked that as an invariant). B4
replaces it with a real ``read_fn`` that routes through the CENTRAL dispatcher
(``ToolExecutor.execute_tool`` — capability-scope enforced) AND reproduces the unservable
denylist so the mock-only ``calendar.create`` post-condition (read_capability
``calendar.get``, backed only by ``query_freebusy``) CANNOT false-CONTRADICT.

Mirrors the autonomous path's already-correct ``step_runner.run_readback``:
  (1) refuse a read_capability in ``_READBACK_UNSERVABLE_CAPABILITIES`` (fail-safe ->
      the verifier resolves UNVERIFIED, never a false CONTRADICTED),
  (2) resolve read_capability -> tool.name via ``list_tools(enabled_only=True)``,
  (3) dispatch through ``execute_tool`` (the dispatcher), never a raw connector call.

The unservable set is IMPORTED from ``step_runner`` (single source) — a drifted copy would
silently re-open the false-CONTRADICT hole (guarded by ``test_unservable_set_is_imported...``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.deep_runtime.readback_readfn as readback_readfn
import src.services.step_runner as step_runner
from src.deep_runtime.readback_readfn import make_readback_read_fn
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from src.services.verification.post_conditions import PostCondition
from src.services.verification.readback import ReadBackVerifier, VerifyVerdict
from tests.conftest import make_mock_settings

USER_ID = "user_1"
WORKSPACE_ID = "ws_1"

POST_CONDITIONS_TARGET = "src.services.verification.post_conditions.POST_CONDITIONS"

TOOL_DEF = {
    "name": "send_email",
    "description": "Send an email on the user's behalf.",
    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}},
}


def _irreversible_risk():
    return SimpleNamespace(reversible=False, blast_radius="external_multiple", risk_level="high")


def _fake_registry(tools):
    """A duck-typed tool_registry: only needs an async ``list_tools(enabled_only=...)``
    returning records carrying ``.name`` + ``.capability`` (same contract as ToolRegistry)."""
    return SimpleNamespace(list_tools=AsyncMock(return_value=tools))


def _servable_post_condition():
    """A SERVABLE post-condition (read_capability NOT in the unservable denylist): confirm
    a created event landed by id-matching the read-back result against write_output."""

    def _matches(read_result, write_input, write_output):
        created_id = (write_output or {}).get("event_id")
        items = read_result if isinstance(read_result, list) else [read_result]
        return any(isinstance(it, dict) and it.get("id") == created_id for it in items)

    return PostCondition(
        read_capability="calendar.read",  # NOT unservable
        read_args=lambda wi, wo: {"event_id": (wo or {}).get("event_id")},
        assertion=_matches,
    )


# ── (c) DRIFT GUARD: the unservable set IS step_runner's (imported, not copied) ──


def test_unservable_set_is_imported_from_step_runner_single_source():
    assert (
        readback_readfn._READBACK_UNSERVABLE_CAPABILITIES
        is step_runner._READBACK_UNSERVABLE_CAPABILITIES
    )
    # Sanity: the mock-only calendar.get IS in the denylist so the footgun stays closed.
    assert "calendar.get" in readback_readfn._READBACK_UNSERVABLE_CAPABILITIES


# ── (d) DISPATCHER routing: execute_tool called with the RESOLVED tool.name ──


async def test_read_fn_routes_through_execute_tool_with_resolved_tool_name():
    """The read goes through the dispatcher (execute_tool), keyed on the tool NAME resolved
    from the read_capability — NOT a raw connector call. Positional call convention matches
    ExecuteToolFn / muldro_tool_dispatcher exactly."""
    execute_tool = AsyncMock(return_value=[{"id": "evt_1"}])
    registry = _fake_registry([SimpleNamespace(name="get_event", capability="calendar.read")])
    read_fn = make_readback_read_fn(
        execute_tool=execute_tool,
        tool_registry=registry,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
    )

    result = await read_fn("calendar.read", {"event_id": "evt_1"})

    assert result == [{"id": "evt_1"}]
    execute_tool.assert_awaited_once_with("get_event", {"event_id": "evt_1"}, USER_ID, WORKSPACE_ID)


# ── (a) SERVABLE: verifier CONFIRMS when the effect is present, CONTRADICTS when absent ──


async def test_servable_verifier_confirms_when_effect_present():
    execute_tool = AsyncMock(return_value=[{"id": "evt_1"}])  # effect PRESENT
    registry = _fake_registry([SimpleNamespace(name="get_event", capability="calendar.read")])
    read_fn = make_readback_read_fn(
        execute_tool=execute_tool,
        tool_registry=registry,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
    )
    verifier = ReadBackVerifier(read_fn)

    with patch.dict(POST_CONDITIONS_TARGET, {"test.write": _servable_post_condition()}):
        verdict = await verifier.verify_step(
            capability="test.write",
            write_input={"calendar_id": "cal_1"},
            write_output={"event_id": "evt_1"},
            risk=_irreversible_risk(),
        )

    assert verdict == VerifyVerdict.CONFIRMED
    execute_tool.assert_awaited_once_with("get_event", {"event_id": "evt_1"}, USER_ID, WORKSPACE_ID)


async def test_servable_verifier_contradicts_when_effect_absent():
    execute_tool = AsyncMock(return_value=[])  # effect ABSENT — real read, no matching id
    registry = _fake_registry([SimpleNamespace(name="get_event", capability="calendar.read")])
    read_fn = make_readback_read_fn(
        execute_tool=execute_tool,
        tool_registry=registry,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
    )
    verifier = ReadBackVerifier(read_fn)

    with patch.dict(POST_CONDITIONS_TARGET, {"test.write": _servable_post_condition()}):
        verdict = await verifier.verify_step(
            capability="test.write",
            write_input={"calendar_id": "cal_1"},
            write_output={"event_id": "evt_1"},
            risk=_irreversible_risk(),
        )

    assert verdict == VerifyVerdict.CONTRADICTED


# ── fail-safe: an executor ERROR DICT (not a raise) must resolve UNVERIFIED, never CONTRADICTED ──


@pytest.mark.parametrize(
    "error_result",
    [
        {"error": "connector down"},  # unknown-tool / failed / unknown-backend shape
        {"error": "Tool 'x' is disabled", "blocked": True},  # disabled shape
        {"status": "error", "error": "internal mcp error"},  # internal-mcp error shape
    ],
)
async def test_executor_error_dict_resolves_unverified_never_contradicted(error_result):
    """execute_tool NEVER raises on a read failure — it returns an error dict. For a SERVABLE
    post-condition that dict would fail the assertion -> false CONTRADICTED. The error-contract
    guard RAISES instead -> the verifier resolves UNVERIFIED (fail-safe, spec §7)."""
    execute_tool = AsyncMock(return_value=error_result)
    registry = _fake_registry([SimpleNamespace(name="get_event", capability="calendar.read")])
    read_fn = make_readback_read_fn(
        execute_tool=execute_tool,
        tool_registry=registry,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
    )
    verifier = ReadBackVerifier(read_fn)

    with patch.dict(POST_CONDITIONS_TARGET, {"test.write": _servable_post_condition()}):
        verdict = await verifier.verify_step(
            capability="test.write",
            write_input={"calendar_id": "cal_1"},
            write_output={"event_id": "evt_1"},
            risk=_irreversible_risk(),
        )

    assert verdict == VerifyVerdict.UNVERIFIED
    assert verdict != VerifyVerdict.CONTRADICTED


async def test_successful_read_dict_with_status_ok_is_not_treated_as_error():
    """A legitimate success dict ({"status": "ok", ...} / raw content) must NOT trip the
    error-contract guard — only error markers do."""
    execute_tool = AsyncMock(return_value={"status": "ok", "result": [{"id": "evt_1"}]})
    registry = _fake_registry([SimpleNamespace(name="get_event", capability="calendar.read")])
    read_fn = make_readback_read_fn(
        execute_tool=execute_tool,
        tool_registry=registry,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
    )

    result = await read_fn("calendar.read", {"event_id": "evt_1"})
    assert result == {"status": "ok", "result": [{"id": "evt_1"}]}


# ── (b) UNSERVABLE: calendar.create -> calendar.get -> read_fn RAISES -> UNVERIFIED ──


async def test_unservable_calendar_get_resolves_unverified_never_contradicted():
    """The footgun: on this branch calendar.get is backed by query_freebusy (free/busy
    ranges, NOT events-by-id). If a LIVE read-back ran, the non-matching result would flip a
    CORRECT calendar.create to CONTRADICTED. The unservable guard RAISES before dispatch, so
    the verifier fails SAFE to UNVERIFIED — never a false CONTRADICTED. execute_tool is a
    freebusy-shaped result that would NOT id-match (proving the guard, not a lucky return)."""
    execute_tool = AsyncMock(
        return_value=[{"start": "2026-01-01T10:00", "end": "2026-01-01T11:00"}]
    )
    registry = _fake_registry([SimpleNamespace(name="query_freebusy", capability="calendar.get")])
    read_fn = make_readback_read_fn(
        execute_tool=execute_tool,
        tool_registry=registry,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
    )
    verifier = ReadBackVerifier(read_fn)

    # Uses the REAL POST_CONDITIONS["calendar.create"] (read_capability=calendar.get).
    verdict = await verifier.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_irreversible_risk(),
    )

    assert verdict == VerifyVerdict.UNVERIFIED
    assert verdict != VerifyVerdict.CONTRADICTED
    execute_tool.assert_not_awaited()  # the guard raised BEFORE any dispatch


async def test_read_fn_raises_for_unservable_capability_before_dispatch():
    execute_tool = AsyncMock()
    registry = _fake_registry([SimpleNamespace(name="query_freebusy", capability="calendar.get")])
    read_fn = make_readback_read_fn(
        execute_tool=execute_tool,
        tool_registry=registry,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
    )

    with pytest.raises(RuntimeError):
        await read_fn("calendar.get", {"event_id": "evt_1"})
    execute_tool.assert_not_awaited()


async def test_read_fn_raises_when_no_tool_serves_capability():
    """No registry tool carries the read_capability -> raise (fail-safe -> UNVERIFIED),
    never a silent None dispatch."""
    execute_tool = AsyncMock()
    registry = _fake_registry([SimpleNamespace(name="other", capability="email.read")])
    read_fn = make_readback_read_fn(
        execute_tool=execute_tool,
        tool_registry=registry,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
    )

    with pytest.raises(RuntimeError):
        await read_fn("calendar.read", {})
    execute_tool.assert_not_awaited()


# ── flag-gated wiring in agent_invoker._build_deep_agent_for ──


def _light_invoker(*, deep_readback_enabled: bool) -> AgentInvoker:
    tool_executor = MagicMock()

    async def _exec(name, args, uid, ws):
        return {"ok": True}

    tool_executor.execute_tool = _exec
    agent = SubAgent(
        name="executor", prompt="p", model_tier="sonnet", capability_scope={"email.send"}
    )
    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", deep_readback_enabled=deep_readback_enabled),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=MagicMock(),
        agents={"executor": agent},
        checkpointer_provider=lambda: None,
    )


async def test_build_deep_agent_wires_real_read_fn_when_flag_on():
    """deep_readback_enabled=True (set EXPLICITLY — MagicMock-truthy hazard): the read-back
    middleware is built with a REAL, callable read_fn (no longer None)."""
    invoker = _light_invoker(deep_readback_enabled=True)
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return MagicMock(name="readback_mw")

    with (
        patch("src.orchestrator.agent_invoker.make_readback_middleware", side_effect=_capture),
        patch(
            "src.orchestrator.agent_invoker.build_deep_agent",
            new=AsyncMock(return_value="DEEP_AGENT"),
        ),
    ):
        result = await invoker._build_deep_agent_for(
            invoker._agents["executor"],
            [TOOL_DEF],
            user_id="u",
            workspace_id="ws",
            thread_id="thread_x",
            authorization_source="autonomous",
            system_prompt="sys",
        )

    assert result == "DEEP_AGENT"
    assert captured.get("read_fn") is not None
    assert callable(captured["read_fn"])


async def test_build_deep_agent_no_readback_middleware_when_flag_off():
    """deep_readback_enabled=False (default): the read-back middleware is NEVER built, so the
    real read_fn is never constructed -> byte-neutral dormant path."""
    invoker = _light_invoker(deep_readback_enabled=False)

    with (
        patch("src.orchestrator.agent_invoker.make_readback_middleware") as mw,
        patch(
            "src.orchestrator.agent_invoker.build_deep_agent",
            new=AsyncMock(return_value="DEEP_AGENT"),
        ),
    ):
        await invoker._build_deep_agent_for(
            invoker._agents["executor"],
            [TOOL_DEF],
            user_id="u",
            workspace_id="ws",
            thread_id="thread_x",
            authorization_source="autonomous",
            system_prompt="sys",
        )

    mw.assert_not_called()
