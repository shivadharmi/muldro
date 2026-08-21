"""P2.1 install seam: ``_build_deep_agent_for(permission_mode=...)`` inserts the
permission_gate into ``extra_middleware`` immediately AFTER ``trust_gate`` for
``ask``/``auto``, and is BYTE-IDENTICAL to before for ``None``/``bypass``.

Offline: ``build_deep_agent`` is patched to an AsyncMock so no real deep-agent compiles;
we only capture its ``extra_middleware`` kwarg. ``make_trust_gate_middleware`` and
``make_permission_gate_middleware`` are patched to return unique sentinels so the gate's
POSITION (right after trust_gate) is provable by identity.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings

INVOKER = "src.orchestrator.agent_invoker"
TOOL_DEF = {
    "name": "send_email",
    "description": "Send an email on the user's behalf.",
    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}},
}
LEAD_SCOPE = {"email.send", "calendar.create"}


def _make_invoker() -> AgentInvoker:
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.execute_tool = MagicMock()

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={},
        checkpointer_provider=lambda: None,
    )


def _agent() -> SubAgent:
    return SubAgent(name="lead", prompt="p", model_tier="sonnet", capability_scope=set(LEAD_SCOPE))


async def _capture_middleware(*, permission_mode):
    """Run ``_build_deep_agent_for`` with sentinel trust/permission gates and return
    ``(extra_middleware_tuple, trust_sentinel, perm_sentinel, make_perm_mock)``."""
    inv = _make_invoker()
    agent = _agent()
    trust_sentinel = object()
    perm_sentinel = object()

    with (
        patch(f"{INVOKER}.build_deep_agent", AsyncMock(return_value=object())) as bda,
        patch(f"{INVOKER}.make_trust_gate_middleware", return_value=trust_sentinel),
        patch(
            f"{INVOKER}.make_permission_gate_middleware", return_value=perm_sentinel
        ) as make_perm,
    ):
        await inv._build_deep_agent_for(
            agent,
            [TOOL_DEF],
            user_id="u",
            workspace_id="ws",
            thread_id="th",
            authorization_source="direct_user_request",
            system_prompt="sys",
            permission_mode=permission_mode,
            presence="absent",
        )
    mw = bda.call_args.kwargs["extra_middleware"]
    return mw, trust_sentinel, perm_sentinel, make_perm


async def test_permission_mode_none_is_byte_identical_no_gate():
    """permission_mode=None → NO permission_gate installed and it is never built."""
    mw, trust_sentinel, perm_sentinel, make_perm = await _capture_middleware(permission_mode=None)

    make_perm.assert_not_called()
    assert perm_sentinel not in mw
    assert trust_sentinel in mw


async def test_permission_mode_bypass_does_not_install_gate():
    """permission_mode='bypass' → NO permission_gate installed (dormant, byte-identical)."""
    mw, _trust, perm_sentinel, make_perm = await _capture_middleware(permission_mode="bypass")

    make_perm.assert_not_called()
    assert perm_sentinel not in mw


async def test_ask_inserts_permission_gate_immediately_after_trust_gate():
    """permission_mode='ask' → permission_gate is inserted RIGHT AFTER trust_gate, adding
    exactly one middleware to the chain."""
    none_mw, _t0, _p0, _m0 = await _capture_middleware(permission_mode=None)
    mw, trust_sentinel, perm_sentinel, make_perm = await _capture_middleware(permission_mode="ask")

    make_perm.assert_called_once()
    assert perm_sentinel in mw
    idx = mw.index(trust_sentinel)
    assert mw[idx + 1] is perm_sentinel, "permission_gate must sit immediately after trust_gate"
    # Additive: exactly one more middleware than the dormant (None) chain.
    assert len(mw) == len(none_mw) + 1


async def test_auto_inserts_permission_gate_immediately_after_trust_gate():
    """permission_mode='auto' → same additive insertion right after trust_gate."""
    mw, trust_sentinel, perm_sentinel, make_perm = await _capture_middleware(permission_mode="auto")

    make_perm.assert_called_once()
    idx = mw.index(trust_sentinel)
    assert mw[idx + 1] is perm_sentinel


async def test_permission_gate_built_with_mode_and_acting_agent_scope():
    """The gate is built with the runtime permission_mode + the agent's capability_scope as
    acting_agent_scope, plus the turn's identity (never LLM-supplied).

    The scope assertion is the load-bearing one. That value is snapshotted onto any prepared
    Approval and is what ``prepared_actions`` replays the recorded call against, so binding it
    to anything wider than the ACTING agent — the plan's capability union, say, which is what
    the old ``lead_scope`` parameter name invited — would retroactively widen the authority a
    staged write is confirmed under. The two-key split on the Approval does not defend against
    that: both keys are written from this one argument.
    """
    inv = _make_invoker()
    agent = _agent()

    with (
        patch(f"{INVOKER}.build_deep_agent", AsyncMock(return_value=object())),
        patch(f"{INVOKER}.make_trust_gate_middleware", return_value=object()),
        patch(f"{INVOKER}.make_permission_gate_middleware", return_value=object()) as make_perm,
    ):
        await inv._build_deep_agent_for(
            agent,
            [TOOL_DEF],
            user_id="u",
            workspace_id="ws",
            thread_id="th",
            authorization_source="direct_user_request",
            system_prompt="sys",
            context_block="CTX",
            permission_mode="auto",
            presence="absent",
        )

    kwargs = make_perm.call_args.kwargs
    assert kwargs["permission_mode"] == "auto"
    assert kwargs["acting_agent_scope"] == agent.capability_scope
    assert kwargs["workspace_id"] == "ws"
    assert kwargs["user_id"] == "u"
    assert kwargs["thread_id"] == "th"
    assert kwargs["agent_name"] == "lead"
    assert kwargs["context_block"] == "CTX"
    assert callable(kwargs["assess_risk"])
    assert callable(kwargs["resolve_capability"])


# ── stream_deep_lead threads permission_mode → _build_deep_agent_for ──────────


async def _one_done_frame(*a, **k):
    yield {
        "event": "agent_done",
        "agent": "lead",
        "text": "ok",
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "tools_called": [],
        "latency_ms": 1,
        "cost_usd": 0.0,
    }


async def _run_stream_lead(*, permission_mode_kwargs: dict) -> dict:
    """Drive ``stream_deep_lead`` with the build + event stream stubbed, returning the
    kwargs ``_build_deep_agent_for`` was called with."""
    inv = _make_invoker()
    lead = _agent()
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    with (
        patch(f"{INVOKER}.stream_deep_agent_events", _one_done_frame),
        patch(f"{INVOKER}.reap_thread", AsyncMock()),
    ):
        _ = [
            f
            async for f in inv.stream_deep_lead(
                lead,
                [TOOL_DEF],
                message="hi",
                context_block="",
                user_id="u",
                workspace_id="ws",
                **permission_mode_kwargs,
            )
        ]
    return inv._build_deep_agent_for.call_args.kwargs


async def test_stream_deep_lead_threads_permission_mode():
    """stream_deep_lead(permission_mode='ask') forwards it into _build_deep_agent_for."""
    kwargs = await _run_stream_lead(permission_mode_kwargs={"permission_mode": "ask"})
    assert kwargs["permission_mode"] == "ask"


async def test_stream_deep_lead_defaults_permission_mode_none():
    """Omitting permission_mode defaults to None → byte-neutral for current callers."""
    kwargs = await _run_stream_lead(permission_mode_kwargs={})
    assert kwargs.get("permission_mode") is None
