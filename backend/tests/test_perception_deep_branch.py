"""Step 10 B6: ``AgentInvoker.call_agent`` (non-stream) gains a ``runtime=="deep"``
branch so the perception path (Perceiver + Librarian) + briefing run on the deep
runtime when ``effective_runtime("perception")=="deep"``.

Design (grounded 2026-07-19):
- Non-stream → final text: mirror ``run_shadow_turn`` — iterate ``_stream_and_reap``
  and capture the ``agent_done`` frame's ``"text"``.
- Headless perception has NO synchronous approver, so the auth-source must never
  ``interrupt()``. We pass ``authorization_source=AUTONOMOUS`` (honest provenance —
  perception ingests untrusted content) + ``pre_approved_capabilities`` = the
  agent's own ``capability_scope`` so every write short-circuits at ``trust_gate``
  as pre-approved (no hang). ``permission_mode`` stays ``None`` (permission_gate
  not installed).
- Legacy (default) path stays byte-neutral.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.deep_runtime.authorization import AuthorizationSource
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings

_LIBRARIAN_SCOPE = {"internal.store_memory", "knowledge.search"}


def _make_invoker(runtime: str) -> AgentInvoker:
    """Minimal AgentInvoker reaching the call_agent runtime branch without real infra.

    Mirrors ``tests/test_agent_invoker_runtime_metric.py::_make_invoker``.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    agent = SubAgent(
        name="librarian",
        prompt="p",
        model_tier="sonnet",
        capability_scope=set(_LIBRARIAN_SCOPE),
    )

    return AgentInvoker(
        settings=make_mock_settings(runtime=runtime),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={"librarian": agent},
    )


async def test_call_agent_legacy_byte_neutral():
    """runtime=legacy: call_agent runs agent_loop as today; deep build NOT called."""
    inv = _make_invoker(runtime="legacy")

    async def _fake_loop(**kw):
        from src.orchestrator.agent_loop import LoopDone

        yield LoopDone(agent="librarian", text="legacy ok")

    with (
        patch(
            "src.orchestrator.agent_invoker.effective_runtime",
            AsyncMock(return_value="legacy"),
        ),
        patch("src.orchestrator.agent_invoker.agent_loop", _fake_loop),
        patch.object(inv, "_build_deep_agent_for") as build_deep,
    ):
        text = await inv.call_agent(
            "librarian", "obs", user_id="u", workspace_id="w", tools_override=[]
        )

    build_deep.assert_not_called()
    assert text == "legacy ok"


async def test_call_agent_deep_returns_agent_done_text():
    """runtime=deep: final text is the agent_done frame's text (mirror run_shadow_turn)."""
    inv = _make_invoker(runtime="deep")

    async def fake_reap(*a, **k):
        yield {"event": "text_delta", "text": "hel"}
        yield {"event": "agent_done", "text": "hello from deep librarian"}

    with (
        patch(
            "src.orchestrator.agent_invoker.effective_runtime",
            AsyncMock(return_value="deep"),
        ),
        patch.object(inv, "_build_deep_agent_for", AsyncMock(return_value=object())),
        patch.object(inv, "_stream_and_reap", fake_reap),
    ):
        text = await inv.call_agent(
            "librarian", "obs", user_id="u", workspace_id="w", tools_override=[]
        )

    assert text == "hello from deep librarian"


async def test_call_agent_deep_uses_autonomous_authsource_and_pre_approved_scope():
    """The deep branch passes AUTONOMOUS provenance + the agent's own scope pre-approved,
    and NO permission_mode — so a headless perception write never interrupts."""
    inv = _make_invoker(runtime="deep")
    captured: dict = {}

    async def fake_build(agent, tools, **kw):
        captured.update(kw)
        return object()

    async def fake_reap(*a, **k):
        yield {"event": "agent_done", "text": "ok"}

    with (
        patch(
            "src.orchestrator.agent_invoker.effective_runtime",
            AsyncMock(return_value="deep"),
        ),
        patch.object(inv, "_build_deep_agent_for", fake_build),
        patch.object(inv, "_stream_and_reap", fake_reap),
    ):
        await inv.call_agent("librarian", "obs", user_id="u", workspace_id="w", tools_override=[])

    assert captured["authorization_source"] == AuthorizationSource.AUTONOMOUS
    assert captured["pre_approved_capabilities"] == frozenset(_LIBRARIAN_SCOPE)
    assert captured.get("permission_mode") is None
