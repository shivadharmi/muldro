"""Step 10D A-5 (Task E): ``AgentInvoker.stream_deep_lead`` + ``effective_chat_runtime``.

``stream_deep_lead`` generalizes ``call_agent_stream``'s ``runtime=="deep"`` branch for a
single synthetic lead that gathers, acts, and replies inline. Two invariants have TEETH:
  (1) PRESENTER_VOICE is ALWAYS applied to the lead (``is_reply_lead=True``) — it must NOT
      be gated on ``_is_reply_lead(lead.name)`` (which is False for the "lead" name);
  (2) the durable thread is reaped IFF the turn did not pause for approval.

DORMANT: no live path calls ``stream_deep_lead`` in 5a — it is callable-but-unwired.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.orchestrator.prompts import PRESENTER_VOICE
from tests.conftest import make_mock_settings


def _lead() -> SubAgent:
    """The synthetic lead — named ``"lead"`` (NOT "presenter"), so any name-based
    ``_is_reply_lead`` gate would drop the Presenter voice."""
    return SubAgent(
        name="lead",
        prompt="LEAD ROLE",
        model_tier="sonnet",
        capability_scope={"email.send"},
        thinking=ThinkingConfig(enabled=True, budget_tokens=4096),
    )


def _make_invoker() -> AgentInvoker:
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
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
    )


def _agent_done_stream(*a, **k):
    async def _gen():
        yield {
            "event": "agent_done",
            "agent": "lead",
            "text": "done",
            "tools_called": [],
        }

    return _gen()


async def test_stream_deep_lead_streams_frames_and_builds_agent():
    """The lead builds a deep agent (patched ``build_deep_agent``) and passes the adapter's
    frames straight through — an ``agent_done`` frame reaches the caller."""
    inv = _make_invoker()

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()) as mock_build,
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_stream),
        patch("src.orchestrator.agent_invoker.agent_loop") as mock_loop,
    ):
        frames = [
            f
            async for f in inv.stream_deep_lead(
                _lead(),
                [],
                message="hi",
                context_block="",
                user_id="u",
                workspace_id="ws",
            )
        ]

    assert any(f["event"] == "agent_done" for f in frames)
    mock_build.assert_awaited()
    mock_loop.assert_not_called()  # never falls back to the legacy loop


async def test_stream_deep_lead_always_appends_presenter_voice():
    """TEETH: the lead ALWAYS gets PRESENTER_VOICE. Captured via ``build_system_message``'s
    argument. This FAILS if the always-on ``is_reply_lead=True`` is reverted to
    ``_is_reply_lead(lead.name)`` (False for "lead" → no append)."""
    inv = _make_invoker()
    captured: dict[str, list[dict]] = {}

    def _capture_build_system_message(blocks):
        captured["blocks"] = blocks
        return "system-msg"

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_stream),
        patch(
            "src.orchestrator.agent_invoker.build_system_message",
            _capture_build_system_message,
        ),
    ):
        _ = [
            f
            async for f in inv.stream_deep_lead(
                _lead(),
                [],
                message="hi",
                context_block="",
                user_id="u",
                workspace_id="ws",
            )
        ]

    joined = "".join(b.get("text", "") for b in captured["blocks"])
    assert PRESENTER_VOICE in joined


def _paused_stream(*a, **k):
    async def _gen():
        yield {"event": "approval_needed", "agent": "lead"}

    return _gen()


async def test_stream_deep_lead_reaps_when_not_paused():
    """A turn that completes without pausing reaps its durable thread."""
    inv = _make_invoker()

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_stream),
        patch("src.orchestrator.agent_invoker.reap_thread", new=AsyncMock()) as mock_reap,
    ):
        _ = [
            f
            async for f in inv.stream_deep_lead(
                _lead(),
                [],
                message="hi",
                context_block="",
                user_id="u",
                workspace_id="ws",
            )
        ]

    mock_reap.assert_awaited_once()


async def test_stream_deep_lead_does_not_reap_when_paused():
    """A turn that pauses on ``approval_needed`` keeps its checkpoint for the resume path —
    reaping it would strand the resume."""
    inv = _make_invoker()

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _paused_stream),
        patch("src.orchestrator.agent_invoker.reap_thread", new=AsyncMock()) as mock_reap,
    ):
        _ = [
            f
            async for f in inv.stream_deep_lead(
                _lead(),
                [],
                message="hi",
                context_block="",
                user_id="u",
                workspace_id="ws",
            )
        ]

    mock_reap.assert_not_awaited()


async def test_stream_deep_lead_increments_deep_runtime_metric():
    """TEETH: the live single-lead path counts itself in AGENT_RUNTIME_CALLS with the fixed
    ``runtime="deep"`` label, so 5b's deep chat traffic is not invisible to the Step-10B
    rollback/adoption signal. Fails if the ``.labels(runtime="deep").inc()`` is dropped."""
    inv = _make_invoker()
    mock_counter = MagicMock()

    with (
        patch("src.orchestrator.agent_invoker.AGENT_RUNTIME_CALLS", mock_counter),
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_stream),
    ):
        _ = [
            f
            async for f in inv.stream_deep_lead(
                _lead(),
                [],
                message="hi",
                context_block="",
                user_id="u",
                workspace_id="ws",
            )
        ]

    mock_counter.labels.assert_called_once_with(runtime="deep")
    mock_counter.labels.return_value.inc.assert_called_once()


async def test_effective_chat_runtime_resolves_via_gate():
    """``effective_chat_runtime`` centralizes the ``effective_runtime("chat", ...)`` call so
    the 5b single-lead branch gates on the SAME resolved value ``call_agent_stream`` uses."""
    inv = _make_invoker()

    with patch(
        "src.orchestrator.agent_invoker.effective_runtime",
        new=AsyncMock(return_value="deep"),
    ) as mock_eff:
        resolved = await inv.effective_chat_runtime()

    assert resolved == "deep"
    mock_eff.assert_awaited_once()
    assert mock_eff.await_args.args[0] == "chat"
    # services=None on this invoker → redis resolves to None (no crash).
    assert mock_eff.await_args.kwargs["redis"] is None


# --- Dormancy proof: 5a adds callable-but-UNWIRED code --------------------------------
def test_chat_processor_does_not_reference_single_lead_symbols():
    """The live chat path must NOT reference the new single-lead symbols in 5a — wiring is
    5b. A grep-style assertion over ``chat_processor.py`` proves 5a is byte-neutral on the
    live path (no import, no call of ``stream_deep_lead`` / ``build_chat_lead``)."""
    src = (
        Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "chat_processor.py"
    ).read_text()
    assert "stream_deep_lead" not in src
    assert "build_chat_lead" not in src
    assert "deep_single_lead" not in src
