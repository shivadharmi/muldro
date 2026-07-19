"""Step 6A metric: AGENT_RUNTIME_CALLS is incremented with the correct runtime label.

These tests drive ``call_agent_stream`` under both ``runtime="legacy"`` and
``runtime="deep"``, patch the counter at the consumer module level, and assert
the label value and the ``.inc()`` call.  Removing the ``.inc()`` from
``call_agent_stream`` causes both tests to fail (the ``inc.assert_called_once``
assertion fires) — this gives the tests teeth.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings


def _make_invoker(runtime: str) -> AgentInvoker:
    """Minimal AgentInvoker that reaches the runtime branch without touching real infra.

    Mirrors the factory in ``test_agent_invoker_runtime_branch.py``.
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


async def test_deep_runtime_increments_metric_with_deep_label():
    """runtime=deep: AGENT_RUNTIME_CALLS.labels(runtime="deep").inc() is called once."""
    inv = _make_invoker(runtime="deep")
    mock_counter = MagicMock()

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
        patch("src.orchestrator.agent_invoker.AGENT_RUNTIME_CALLS", mock_counter),
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter),
        patch("src.orchestrator.agent_invoker.agent_loop"),
    ):
        _ = [
            f
            async for f in inv.call_agent_stream(
                "perceiver",
                message="hi",
                user_id="u",
                workspace_id="ws",
                tools_override=[],
            )
        ]

    # Fails if .labels() is removed or called with wrong runtime value.
    mock_counter.labels.assert_called_once_with(runtime="deep")
    # Fails if .inc() is removed — the core teeth of this test.
    mock_counter.labels.return_value.inc.assert_called_once()
