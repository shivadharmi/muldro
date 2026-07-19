"""Step 6A blast-radius: AgentInvoker.call_agent_stream branches on JARVIS_RUNTIME.

The DEFAULT (``legacy``) path MUST run the existing ``agent_loop`` unchanged (this is
the live chat entry path). Only when ``self._settings.runtime == "deep"`` does the
invoker run the Deep Agents adapter instead. The branch is controlled by the settings
INJECTED INTO the invoker (``self._settings.runtime``), not a module singleton, so these
tests flip it via ``make_mock_settings(runtime=...)``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings


def _make_invoker(runtime: str) -> AgentInvoker:
    """Build a real AgentInvoker with the minimal mocks to reach the runtime branch.

    ``tools_override=[]`` short-circuits ``_resolve_tools`` to
    ``apply_cache_control_to_tools([])`` (returns ``[]``), and the perceiver agent has an
    empty ``capability_scope`` (read-only, no write-guard concerns). ``assemble_context``
    returns ``""`` so ``system_blocks`` builds. All of this lets the code reach the branch
    without touching the legacy/deep bodies themselves (those are patched per-test).
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
    )


async def test_deep_runtime_uses_adapter():
    """runtime=deep: the invoker builds a deep agent + streams the adapter, never agent_loop."""
    inv = _make_invoker(runtime="deep")

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
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter),
        patch("src.orchestrator.agent_invoker.agent_loop") as mock_loop,
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
    mock_loop.assert_not_called()
    mock_build.assert_awaited()
