"""Step 8 P2: flag-wiring for the slim JIT context pack + negative controls.

``deep_context_jit`` is dormant by default (``False``). It only takes effect on the
deep chat runtime (``JARVIS_RUNTIME=deep``) AND only for agents in
``JIT_ENABLED_AGENTS`` (the agents that hold the JIT read tools). Presenter/Executor
lack world-model reads, so they keep the eager pack even when the flag is on.

These tests drive the REAL call site (``agent_invoker.py:514``, inside
``call_agent_stream``) rather than hand-recomputing the boolean, so a regression in
the wiring itself (not just in ``JIT_ENABLED_AGENTS``) would be caught.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from src.orchestrator.context_assembler import JIT_ENABLED_AGENTS
from src.orchestrator.prompts import JARVIS_SOUL_CORE
from tests.conftest import make_mock_settings


def test_jit_enabled_agents_excludes_presenter_and_executor():
    assert JIT_ENABLED_AGENTS == {"planner", "perceiver", "librarian"}
    assert "presenter" not in JIT_ENABLED_AGENTS
    assert "executor" not in JIT_ENABLED_AGENTS


def _make_invoker(
    runtime: str, agent_name: str = "planner", **settings_overrides
) -> tuple[AgentInvoker, AsyncMock]:
    """Build a real AgentInvoker with the minimal mocks to reach the
    ``assemble_context`` call site inside ``call_agent_stream``.

    Mirrors the harness in ``tests/test_agent_invoker_deep_hardening.py``, but
    replaces ``context.assemble_context`` with an ``AsyncMock`` we can inspect
    afterward for the ``jit`` kwarg it was actually called with.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools

    context = MagicMock()
    captured_assemble = AsyncMock(return_value="")
    context.assemble_context = captured_assemble

    agent = SubAgent(name=agent_name, prompt="p", model_tier="sonnet", capability_scope=set())

    invoker = AgentInvoker(
        settings=make_mock_settings(runtime=runtime, **settings_overrides),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={agent_name: agent},
        checkpointer_provider=lambda: object(),
    )
    return invoker, captured_assemble


async def _drive_deep(invoker: AgentInvoker, agent_name: str) -> None:
    """Drive ``call_agent_stream`` on the deep branch to completion, with the deep-
    agent internals mocked (same minimal set as the passing tests in
    ``test_agent_invoker_deep_hardening.py``) so the stream finishes cleanly.
    """

    async def _fake_adapter(*a, **k):
        yield {
            "event": "agent_done",
            "agent": agent_name,
            "text": "ok",
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "tools_called": [],
            "latency_ms": 1,
            "cost_usd": 0.0,
        }

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch(
            "src.orchestrator.agent_invoker.make_jarvis_tool_dispatcher",
            return_value=object(),
        ),
        patch(
            "src.orchestrator.agent_invoker.make_trust_gate_middleware",
            return_value=object(),
        ),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter),
    ):
        _ = [
            f
            async for f in invoker.call_agent_stream(
                agent_name,
                message="hi",
                user_id="u",
                workspace_id="ws",
                tools_override=[],
            )
        ]


async def test_flag_off_deep_planner_calls_assemble_context_with_jit_false():
    """Flag-off (default) deep run: assemble_context is called with jit=False — the
    dormancy proof for Step 8 P2 at the REAL call site (agent_invoker.py:514)."""
    invoker, captured = _make_invoker(runtime="deep", agent_name="planner")

    await _drive_deep(invoker, "planner")

    captured.assert_awaited_once()
    assert captured.await_args.kwargs["jit"] is False


async def test_flag_on_deep_planner_calls_assemble_context_with_jit_true():
    """Flag-on deep run for a JIT-enabled agent (planner): assemble_context is
    called with jit=True."""
    invoker, captured = _make_invoker(runtime="deep", agent_name="planner", deep_context_jit=True)

    await _drive_deep(invoker, "planner")

    captured.assert_awaited_once()
    assert captured.await_args.kwargs["jit"] is True


async def test_legacy_runtime_calls_assemble_context_with_jit_false_regardless_of_flag():
    """Legacy runtime: jit is always False even with deep_context_jit=True — the flag
    only ever matters on the deep branch."""
    invoker, captured = _make_invoker(runtime="legacy", agent_name="planner", deep_context_jit=True)

    async def _fake_loop(**kw):
        from src.orchestrator.agent_loop import LoopDone

        yield LoopDone(agent="planner", text="ok")

    with (
        patch("src.orchestrator.agent_invoker.agent_loop", _fake_loop),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events") as mock_deep,
    ):
        _ = [
            f
            async for f in invoker.call_agent_stream(
                "planner",
                message="hi",
                user_id="u",
                workspace_id="ws",
                tools_override=[],
            )
        ]

    mock_deep.assert_not_called()
    captured.assert_awaited_once()
    assert captured.await_args.kwargs["jit"] is False


def test_build_system_prompt_two_block_cache_layout_survives_slim_context():
    """Task 4.2: structural prompt-cache-layout guard.

    ``build_system_prompt`` (``agent_invoker.py``) must keep the stable soul+role
    text in block 0 WITH ``cache_control`` (so it is the durable, cached prefix),
    and put the (now possibly JIT-slim) context in block 1 WITHOUT
    ``cache_control`` — the slim rendering must not disturb this layout. The
    LIVE ``cache_read_input_tokens>0`` proof is deferred to the activation gate;
    this is the structural proof only (no live API call).
    """
    invoker, _ = _make_invoker(runtime="legacy", agent_name="presenter")
    agent = SubAgent(name="presenter", prompt="role", model_tier="sonnet", capability_scope=set())

    slim_context = (
        "## Known Entities\n- Acme (org)\n\n## Retrieving More Context\nUse `get_entity` ..."
    )

    blocks = invoker.build_system_prompt(agent, context=slim_context)

    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert JARVIS_SOUL_CORE in blocks[0]["text"]
    assert "role" in blocks[0]["text"]
    assert blocks[1]["text"] == slim_context
    assert "cache_control" not in blocks[1]
