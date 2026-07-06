"""Step 6A.5 blast-radius: AgentInvoker.call_agent_stream deep branch wires
shells + central dispatcher + SystemMessage + durable checkpointer.

Tests confirm:
- runtime="deep": build_tool_shells, make_jarvis_tool_dispatcher, build_system_message,
  and checkpointer_provider are all wired correctly into build_deep_agent.
- runtime="legacy": agent_loop is still called and the deep adapter is never touched
  (legacy path byte-behavior-identical after adding checkpointer_provider param).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import SystemMessage

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings


def _make_invoker(runtime: str, checkpointer_provider=None) -> AgentInvoker:
    """Build a real AgentInvoker with the minimal mocks to reach the runtime branch.

    Extends the pattern from test_agent_invoker_runtime_branch._make_invoker with an
    optional ``checkpointer_provider`` param to test the durable-checkpointer wiring.
    ``tools_override=[]`` short-circuits ``_resolve_tools``. ``assemble_context`` returns
    ``""`` so system_blocks build cleanly.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    agent = SubAgent(name="perceiver", prompt="p", model_tier="sonnet", capability_scope=set())

    return AgentInvoker(
        settings=make_mock_settings(runtime=runtime),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={"perceiver": agent},
        checkpointer_provider=checkpointer_provider,
    )


async def test_deep_branch_uses_shells_dispatcher_systemmessage_and_provider():
    """runtime=deep: build_deep_agent receives shells, dispatcher, SystemMessage, provider saver."""
    sentinel_saver = object()
    sentinel_mw = object()

    inv = _make_invoker(runtime="deep", checkpointer_provider=lambda: sentinel_saver)

    async def _fake_adapter(*a, **k):
        yield {
            "event": "agent_done",
            "agent": "perceiver",
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
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()) as mock_build,
        patch(
            "src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]
        ) as mock_shells,
        patch(
            "src.orchestrator.agent_invoker.make_jarvis_tool_dispatcher",
            return_value=sentinel_mw,
        ) as mock_dispatcher,
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter),
    ):
        frames = [
            f
            async for f in inv.call_agent_stream(
                "perceiver",
                message="hi",
                user_id="u",
                workspace_id="ws",
                tools_override=[],
            )
        ]

    assert any(f["event"] == "agent_done" for f in frames)

    # (a) build_tool_shells was called once — tools become inert schema shells.
    mock_shells.assert_called_once()

    # Inspect keyword args passed to build_deep_agent.
    kw = mock_build.call_args.kwargs

    # (a) Shells are passed as the second positional arg (args[1]) to build_deep_agent.
    assert mock_build.call_args.args[1] == ["SHELL"], (
        f"expected shells ['SHELL'] as positional arg[1], got {mock_build.call_args.args!r}"
    )

    # (b) sentinel_mw dispatcher is inside extra_middleware.
    assert sentinel_mw in tuple(kw["extra_middleware"]), (
        f"expected sentinel_mw in extra_middleware, got {kw.get('extra_middleware')!r}"
    )

    # (c) system_prompt is a structured SystemMessage (not a flat string).
    assert isinstance(kw["system_prompt"], SystemMessage), (
        f"expected SystemMessage, got {type(kw['system_prompt'])}"
    )

    # (d) checkpointer is exactly what checkpointer_provider() returned.
    assert kw["checkpointer"] is sentinel_saver, (
        f"expected sentinel_saver, got {kw.get('checkpointer')!r}"
    )

    # (b') the dispatcher is built with closure-bound provenance — user_id/workspace_id
    # come from the call args, and execute_tool from the invoker's own tool_executor
    # (never LLM-supplied). This locks the security invariant an LLM cannot spoof.
    disp_kw = mock_dispatcher.call_args.kwargs
    assert disp_kw["user_id"] == "u"
    assert disp_kw["workspace_id"] == "ws"
    assert disp_kw["execute_tool"] is inv._tool_executor.execute_tool


async def test_deep_branch_falls_back_to_memorysaver_when_provider_returns_none():
    """runtime=deep with no durable provider (the live default until Task 7 wires it):
    the seam falls back to an in-process MemorySaver so a thread_id always has a store."""
    from langgraph.checkpoint.memory import MemorySaver

    inv = _make_invoker(runtime="deep", checkpointer_provider=None)

    async def _fake_adapter(*a, **k):
        yield {
            "event": "agent_done",
            "agent": "perceiver",
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
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()) as mock_build,
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch(
            "src.orchestrator.agent_invoker.make_jarvis_tool_dispatcher",
            return_value=object(),
        ),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter),
    ):
        frames = [
            f
            async for f in inv.call_agent_stream(
                "perceiver",
                message="hi",
                user_id="u",
                workspace_id="ws",
                tools_override=[],
            )
        ]

    assert any(f["event"] == "agent_done" for f in frames)
    assert isinstance(mock_build.call_args.kwargs["checkpointer"], MemorySaver)


async def test_legacy_runtime_unchanged_after_new_param():
    """runtime=legacy: agent_loop is still the path; the deep adapter is never called.

    This mirrors test_agent_invoker_runtime_branch.test_legacy_runtime_uses_agent_loop
    to confirm the new checkpointer_provider param does NOT alter legacy behaviour.
    """
    inv = _make_invoker(runtime="legacy")

    async def _fake_loop(**kw):
        from src.orchestrator.agent_loop import LoopDone

        yield LoopDone(agent="perceiver", text="ok")

    with (
        patch("src.orchestrator.agent_invoker.agent_loop", _fake_loop),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events") as mock_deep,
    ):
        frames = [
            f
            async for f in inv.call_agent_stream(
                "perceiver",
                message="hi",
                user_id="u",
                workspace_id="ws",
                tools_override=[],
            )
        ]

    assert any(f["event"] == "agent_done" for f in frames)
    mock_deep.assert_not_called()
