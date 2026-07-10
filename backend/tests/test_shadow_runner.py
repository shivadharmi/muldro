"""Step 10B Task 3b: ShadowRunner — the LOAD-BEARING shadow-compare wiring.

The shadow harness runs the NON-authoritative agent runtime alongside the
authoritative one, SAMPLED + ASYNC + isolated + throwaway-session, diffing
read-only decision outputs via ``DivergenceComparator`` and emitting
``shadow_divergence``. Default OFF (``shadow_sample_rate=0.0``).

Four requirements under test here:
  1. Injection seam REACHED — a deep-shadow WRITE via the REAL
     ``_build_deep_agent_for`` build path records ZERO real dispatches
     (proven by driving the REAL chain, mirroring
     ``tests/deep_runtime/test_readback_e2e.py``).
  2. Isolation — a shadow run that RAISES is swallowed (logged), never
     propagating to the authoritative turn.
  3. Byte-neutral when OFF — with ``shadow_sample_rate=0.0``,
     ``invoker.run_shadow_turn`` is never called.
  4. Comparator emits — a detected divergence increments
     ``jarvis_shadow_divergence_total`` by kind.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.deep_runtime import agent_builder
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from src.orchestrator.divergence import ShadowDecision
from src.orchestrator.shadow_runner import ShadowRunner, _IntentRecordingShadowExecutor
from src.orchestrator.shadow_tool_executor import ShadowToolExecutor
from src.services.metrics_service import SHADOW_DIVERGENCE, MetricsService
from tests.conftest import make_mock_settings
from tests.deep_runtime.test_readback_e2e import (
    _STUB_RISK,
    CAP_SCOPE_TOOL_REGISTRY,
    GET_OR_ASSESS_RISK,
    TRUST_GATE_TOOL_REGISTRY,
    ScriptedModel,
    _FakeRegistry,
    _lead_write_turns,
    _write_recorder,
)

WS = "ws_shadow_runner"
USER = "user_shadow_runner"


def _fake_db_factory():
    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _auth_decision(**overrides) -> ShadowDecision:
    defaults = dict(route="presenter", final_text="hi", write_intents=frozenset())
    defaults.update(overrides)
    return ShadowDecision(**defaults)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Comparator emits — a divergence recorded by the injected (mocked)
#    run_shadow_turn increments the metric.
# ═══════════════════════════════════════════════════════════════════════════
async def test_injected_divergence_emitted():
    invoker = MagicMock()
    invoker.run_shadow_turn = AsyncMock(
        return_value=ShadowDecision(
            route="presenter",
            final_text="hi",
            write_intents=frozenset({"email.send:gmail_send"}),
        )
    )
    settings = make_mock_settings(shadow_sample_rate=1.0, runtime="legacy")
    runner = ShadowRunner(
        invoker, settings, tool_executor=MagicMock(), db_factory_provider=lambda: _fake_db_factory()
    )

    before = MetricsService.read_counter_total(SHADOW_DIVERGENCE, kind="write_intent_set")
    await runner.maybe_run_shadow(
        agent_name="presenter",
        message="hello",
        user_id=USER,
        workspace_id=WS,
        authoritative_decision=_auth_decision(),
    )
    after = MetricsService.read_counter_total(SHADOW_DIVERGENCE, kind="write_intent_set")

    assert after == before + 1
    invoker.run_shadow_turn.assert_awaited_once()
    _, kwargs = invoker.run_shadow_turn.call_args
    assert kwargs["runtime"] == "deep"  # authoritative=legacy -> shadow runs the opposite


# ═══════════════════════════════════════════════════════════════════════════
# 2. Isolation — a shadow run that raises is swallowed, never propagates.
# ═══════════════════════════════════════════════════════════════════════════
async def test_shadow_exception_is_isolated_and_swallowed(caplog):
    invoker = MagicMock()
    invoker.run_shadow_turn = AsyncMock(side_effect=RuntimeError("shadow blew up"))
    settings = make_mock_settings(shadow_sample_rate=1.0, runtime="legacy")
    runner = ShadowRunner(
        invoker, settings, tool_executor=MagicMock(), db_factory_provider=lambda: _fake_db_factory()
    )

    with caplog.at_level(logging.WARNING, logger="src.orchestrator.shadow_runner"):
        # Must NOT raise — isolation is the point.
        await runner.maybe_run_shadow(
            agent_name="presenter",
            message="hello",
            user_id=USER,
            workspace_id=WS,
            authoritative_decision=_auth_decision(),
        )

    assert any("shadow" in rec.message.lower() for rec in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Byte-neutral when OFF — default sample rate never calls run_shadow_turn.
# ═══════════════════════════════════════════════════════════════════════════
async def test_byte_neutral_when_sample_rate_is_zero():
    invoker = MagicMock()
    invoker.run_shadow_turn = AsyncMock()
    settings = make_mock_settings(shadow_sample_rate=0.0, runtime="legacy")
    runner = ShadowRunner(
        invoker, settings, tool_executor=MagicMock(), db_factory_provider=lambda: _fake_db_factory()
    )

    await runner.maybe_run_shadow(
        agent_name="presenter",
        message="hello",
        user_id=USER,
        workspace_id=WS,
        authoritative_decision=_auth_decision(),
    )

    invoker.run_shadow_turn.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# 4. Injection-seam TEETH — a real AgentInvoker, a REAL _build_deep_agent_for
#    build, a SPY real tool executor, and an injected ShadowToolExecutor.
#    Asserts the SPY (invoker's real tool_executor) records ZERO dispatches
#    for the irreversible write — proving run_shadow_turn's `tool_executor`
#    param actually reaches the dispatcher instead of being bypassed by a
#    hard-wired real executor. Mirrors tests/deep_runtime/test_readback_e2e.py.
# ═══════════════════════════════════════════════════════════════════════════
# Process-global _HARNESS_PROFILES restore (copied from test_readback_e2e.py's
# critical-hazard fixture) — building the real deep lead below registers the
# "anthropic:claude-sonnet-4-6" harness profile as a side effect; snapshot +
# restore so it never leaks into the wider sonnet-lead deep suite.
_GP_TEST_KEYS = ("anthropic:claude-sonnet-4-6", "anthropic:claude-opus-4-8")


@pytest.fixture(autouse=True)
def _restore_harness_profiles():
    from deepagents.profiles.harness.harness_profiles import (
        _HARNESS_PROFILES,
        _ensure_harness_profiles_loaded,
    )

    _ensure_harness_profiles_loaded()
    saved = {k: _HARNESS_PROFILES.get(k) for k in _GP_TEST_KEYS}
    try:
        yield
    finally:
        for key, prev in saved.items():
            if prev is None:
                _HARNESS_PROFILES.pop(key, None)
            else:
                _HARNESS_PROFILES[key] = prev


def _lead() -> SubAgent:
    return SubAgent(
        name="presenter",
        prompt="You are the lead.",
        model_tier="sonnet",
        capability_scope={"email.send"},
    )


def _make_real_invoker(*, real_spy_calls: list) -> AgentInvoker:
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])
    # This is the SPY: if the write reaches it, the injection seam is broken.
    tool_executor.execute_tool = _write_recorder(real_spy_calls)

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    lead = _lead()
    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", deep_readback_enabled=False),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _fake_db_factory(),
        tool_executor=tool_executor,
        context=context,
        agents={lead.name: lead},
        checkpointer_provider=lambda: None,
    )


async def test_injection_seam_teeth_real_build_path_suppresses_write():
    real_spy_calls: list = []
    shadow_backing_calls: list = []
    lead_fake = ScriptedModel(_lead_write_turns())
    invoker = _make_real_invoker(real_spy_calls=real_spy_calls)

    async def _resolve(name: str) -> str | None:
        return {"email_send": "email.send"}.get(name)

    shadow_backing = MagicMock()
    shadow_backing.execute_tool = _write_recorder(shadow_backing_calls)
    shadow_exec = _IntentRecordingShadowExecutor(ShadowToolExecutor(shadow_backing, _resolve))

    with (
        patch.object(agent_builder, "build_chat_model", lambda a: lead_fake),
        patch(CAP_SCOPE_TOOL_REGISTRY, _FakeRegistry),
        patch(TRUST_GATE_TOOL_REGISTRY, _FakeRegistry),
        patch("src.deep_runtime.middleware.unavailable_server.ToolRegistry", _FakeRegistry),
        patch(GET_OR_ASSESS_RISK, AsyncMock(return_value=_STUB_RISK)),
    ):
        decision = await invoker.run_shadow_turn(
            "presenter",
            "email a@b.com",
            user_id=USER,
            workspace_id=WS,
            runtime="deep",
            tool_executor=shadow_exec,
        )

    # THE TEETH: the write never reached the REAL dispatch (invoker's own
    # tool_executor) — it was suppressed by the INJECTED ShadowToolExecutor.
    assert real_spy_calls == [], (
        f"the write reached the REAL executor — injection seam bypassed; calls={real_spy_calls}"
    )
    # The shadow's own backing executor is never called either (it's a WRITE,
    # so ShadowToolExecutor suppresses before ever reaching its backing).
    assert shadow_backing_calls == []
    # The suppression signal was captured as a write-intent.
    assert "email.send:email_send" in shadow_exec.write_intents
    assert decision.route == "presenter"
    assert decision.write_intents == frozenset({"email.send:email_send"})
