"""``AgentInvoker.call_agent`` (non-stream) runs the perception path (Perceiver +
Librarian) + briefing on the deep runtime — the ONLY runtime (Step 11 Phase 4).

Design:
- Non-stream → final text: iterate ``_stream_and_reap`` and capture the ``agent_done``
  frame's ``"text"`` (mirrors ``run_shadow_turn``).
- Headless perception has NO synchronous approver, so the auth-source must never
  ``interrupt()``. We pass ``authorization_source=AUTONOMOUS`` (honest provenance —
  perception ingests untrusted content) + ``pre_approved_capabilities`` = the agent's
  own ``capability_scope`` so every write short-circuits at ``trust_gate`` as
  pre-approved (no hang). ``permission_mode`` stays ``None`` (permission_gate not installed).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.deep_runtime.authorization import AuthorizationSource
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings

_LIBRARIAN_SCOPE = {"internal.store_memory", "knowledge.search"}


def _make_invoker() -> AgentInvoker:
    """Minimal AgentInvoker reaching call_agent's deep body without real infra."""
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
        settings=make_mock_settings(),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={"librarian": agent},
    )


async def test_call_agent_returns_agent_done_text():
    """Final text is the agent_done frame's text (mirror run_shadow_turn)."""
    inv = _make_invoker()

    async def fake_reap(*a, **k):
        yield {"event": "text_delta", "text": "hel"}
        yield {"event": "agent_done", "text": "hello from deep librarian"}

    with (
        patch.object(inv, "_build_deep_agent_for", AsyncMock(return_value=object())),
        patch.object(inv, "_stream_and_reap", fake_reap),
    ):
        text = await inv.call_agent(
            "librarian", "obs", user_id="u", workspace_id="w", tools_override=[]
        )

    assert text == "hello from deep librarian"


async def test_call_agent_uses_autonomous_authsource_and_pre_approved_scope():
    """The deep body passes AUTONOMOUS provenance + the agent's own scope pre-approved,
    and NO permission_mode — so a headless perception write never interrupts."""
    inv = _make_invoker()
    captured: dict = {}

    async def fake_build(agent, tools, **kw):
        captured.update(kw)
        return object()

    async def fake_reap(*a, **k):
        yield {"event": "agent_done", "text": "ok"}

    with (
        patch.object(inv, "_build_deep_agent_for", fake_build),
        patch.object(inv, "_stream_and_reap", fake_reap),
    ):
        await inv.call_agent("librarian", "obs", user_id="u", workspace_id="w", tools_override=[])

    assert captured["authorization_source"] == AuthorizationSource.AUTONOMOUS
    assert captured["pre_approved_capabilities"] == frozenset(_LIBRARIAN_SCOPE)
    assert captured.get("permission_mode") is None


async def test_call_agent_error_frame_returns_error_envelope():
    """A deep stream that errors (no agent_done) returns the error envelope, not "" —
    else the briefing facade would ship a silent empty briefing."""
    inv = _make_invoker()

    async def fake_reap(*a, **k):
        yield {"event": "error", "agent": "librarian", "message": "boom"}

    with (
        patch.object(inv, "_build_deep_agent_for", AsyncMock(return_value=object())),
        patch.object(inv, "_stream_and_reap", fake_reap),
    ):
        text = await inv.call_agent(
            "librarian", "obs", user_id="u", workspace_id="w", tools_override=[]
        )

    assert text == "[Agent error: boom]"


async def test_call_agent_empty_stream_returns_empty_string():
    """No agent_done and no error frame → returns "" (no false error envelope)."""
    inv = _make_invoker()

    async def fake_reap(*a, **k):
        yield {"event": "text_delta", "text": "partial"}

    with (
        patch.object(inv, "_build_deep_agent_for", AsyncMock(return_value=object())),
        patch.object(inv, "_stream_and_reap", fake_reap),
    ):
        text = await inv.call_agent(
            "librarian", "obs", user_id="u", workspace_id="w", tools_override=[]
        )

    assert text == ""
