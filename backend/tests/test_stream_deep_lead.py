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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.contracts import PlanStep
from src.orchestrator import lead_builder
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
def test_chat_processor_wires_single_lead_branch():
    """P1 Task B (5b) wired the deep single-lead chat path into ``_process_core``;
    P2.3 WIDENED it to the full chat permission model. ``chat_processor.py`` MUST reference
    the single-lead symbols, resolve an EFFECTIVE mode (checking ``deep_single_lead`` FIRST
    so the default short-circuits with zero extra I/O), and branch on that resolved mode
    across ``bypass``/``ask``/``auto`` — see ``tests/test_chat_single_lead.py`` for the
    behavioral coverage."""
    src = (
        Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "chat_processor.py"
    ).read_text()
    assert "stream_deep_lead" in src
    assert "build_chat_lead" in src
    assert "deep_single_lead" in src
    # P2.3: the branch is gated on a resolved effective mode (fail-safe downgrades), not a
    # bare exact-equality on "bypass". Assert the wiring SYMBOLS (not an exact expression
    # string, which would break on a benign refactor) — behavioral coverage lives in
    # tests/test_chat_single_lead.py.
    assert "effective_mode" in src
    assert "workspace_allows_bypass" in src
    assert "has_durable_checkpointer" in src


# --- P1 A2: stream_deep_lead resolves tools internally when tools is None --------------
async def test_stream_deep_lead_resolves_tools_when_none():
    """A1 P1 (SECURITY): ``tools=None`` (omitted) triggers ``_resolve_tools(lead, ws, None)``
    so the offered tool set is derived from the lead's OWN scope (offered ⊆ enforced by
    construction) — the caller cannot pass an inconsistent tool set. The resolved tools are
    forwarded on to the deep build (through ``build_tool_shells``)."""
    inv = _make_invoker()
    lead = _lead()
    sentinel_tools = [{"name": "email_send"}]

    with (
        patch.object(
            inv, "_resolve_tools", new=AsyncMock(return_value=sentinel_tools)
        ) as spy_resolve,
        patch(
            "src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]
        ) as spy_shells,
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_stream),
    ):
        _ = [
            f
            async for f in inv.stream_deep_lead(
                lead,
                None,
                message="hi",
                context_block="",
                user_id="u",
                workspace_id="ws",
            )
        ]

    spy_resolve.assert_awaited_once_with(lead, "ws", None)
    # The resolved tools (not None) reach the deep build.
    spy_shells.assert_called_once_with(sentinel_tools)


async def test_stream_deep_lead_explicit_empty_tools_skips_resolve():
    """An EXPLICIT ``[]`` still means "no tools" — only ``None`` triggers the internal
    resolve. This pins the boundary so 5a's existing ``[]``-passing tests stay valid."""
    inv = _make_invoker()
    lead = _lead()

    with (
        patch.object(inv, "_resolve_tools", new=AsyncMock()) as spy_resolve,
        patch(
            "src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]
        ) as spy_shells,
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_stream),
    ):
        _ = [
            f
            async for f in inv.stream_deep_lead(
                lead,
                [],
                message="hi",
                context_block="",
                user_id="u",
                workspace_id="ws",
            )
        ]

    spy_resolve.assert_not_awaited()
    spy_shells.assert_called_once_with([])


# --- P1 A3: the chat lead ALWAYS fail-closes its write lock on Redis-down --------------
async def test_stream_deep_lead_forces_write_lock_fail_closed():
    """A3 (SECURITY): the ungated chat single-lead path serializes its writes even when the
    per-caller ``write_lock_require_redis`` default is False. ``stream_deep_lead`` passes
    ``require_write_lock=True``, so ``make_write_lock_middleware`` is built with
    ``require_redis=True`` — writes never execute unserialized while Redis is down. TEETH:
    the mock settings default is False, so a True here can only come from the forced flag."""
    inv = _make_invoker()  # settings.write_lock_require_redis == False (conftest default)

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_stream),
        patch(
            "src.orchestrator.agent_invoker.make_write_lock_middleware", return_value=object()
        ) as mock_wl,
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

    assert mock_wl.call_args.kwargs["require_redis"] is True


# --- P1 A1: AgentInvoker.build_chat_lead wrapper --------------------------------------
class _FakeCM:
    """Async context manager yielding a fake DB session (mirrors test_lead_builder)."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _make_invoker_with_agents(agents, db_factory, *, cheap_mode=False) -> AgentInvoker:
    """AgentInvoker whose agent set + cheap_mode + db_factory are the single source of truth
    the ``build_chat_lead`` wrapper reads from."""
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")
    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", cheap_mode=cheap_mode),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: db_factory,
        tool_executor=tool_executor,
        context=context,
        agents=agents,
    )


async def test_build_chat_lead_uses_invoker_agents_and_returns_lead():
    """The wrapper builds the plan-bounded ``lead`` SubAgent, feeding ``derive_lead_scope``
    the INVOKER's OWN agent set (the same perceiver the per-step path uses)."""
    perceiver = SubAgent(
        name="perceiver", prompt="p", model_tier="sonnet", capability_scope={"email.read"}
    )
    agents = {"perceiver": perceiver}

    def _db_factory():
        return _FakeCM(db=object())

    inv = _make_invoker_with_agents(agents, _db_factory, cheap_mode=False)

    fake_resolver = SimpleNamespace(capabilities_for_step=AsyncMock(side_effect=lambda c: {c}))
    step = PlanStep(description="email.send", capability="email.send", actor="jarvis")
    real_derive = lead_builder.derive_lead_scope

    with (
        patch("src.orchestrator.lead_builder.CapabilityResolver", return_value=fake_resolver),
        patch("src.orchestrator.lead_builder.derive_lead_scope", wraps=real_derive) as spy_derive,
    ):
        lead = await inv.build_chat_lead([step], "ws")

    assert lead.name == "lead"
    assert lead.capability_scope == {"email.send"}
    # derive_lead_scope received the invoker's own agent set (positional arg[2]).
    assert spy_derive.await_args.args[2] is agents


async def test_build_chat_lead_forwards_invoker_cheap_mode():
    """The wrapper forwards ``self._settings.cheap_mode`` — cheap mode halves the lead's
    thinking budget (4096 -> 2048) while keeping the sonnet tier."""
    agents = {
        "perceiver": SubAgent(
            name="perceiver", prompt="p", model_tier="sonnet", capability_scope=set()
        )
    }

    def _db_factory():
        return _FakeCM(db=object())

    inv = _make_invoker_with_agents(agents, _db_factory, cheap_mode=True)

    fake_resolver = SimpleNamespace(capabilities_for_step=AsyncMock(side_effect=lambda c: {c}))
    step = PlanStep(description="email.send", capability="email.send", actor="jarvis")

    with patch("src.orchestrator.lead_builder.CapabilityResolver", return_value=fake_resolver):
        lead = await inv.build_chat_lead([step], "ws")

    assert lead.name == "lead"
    assert lead.model_tier == "sonnet"
    assert lead.thinking.budget_tokens == 2048
