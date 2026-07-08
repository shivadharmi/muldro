"""Step 7B2 P4 blast-radius: AgentInvoker.call_agent_stream deep seam wires the
read-only Perceiver delegate — flag-gated + dormant.

The delegate layer is DORMANT behind ``deep_delegates_enabled`` (default False).
These tests lock the seam's two arms:

* Flag ON  — the deep lead is built with ``subagents=[<perceiver delegate>]`` and the
  ambient general-purpose ``task`` child is GP-disabled on both the lead's and the
  delegate's built (direct-Anthropic) model ids. The delegate config is sourced from
  the in-memory ``build_agent_set(AGENTS, cheap_mode)`` singleton (thinking preserved),
  NOT ``self._agents`` (which ``load_as_sub_agents`` may overwrite, dropping thinking).
* Flag OFF — the byte-identical-dormancy proof: ``subagents == ()`` reaches
  ``_build_deep_agent_for`` (→ ``build_deep_agent(subagents=())`` → ``create_deep_agent
  (subagents=None)`` = 7B1 behaviour), and NEITHER ``build_read_only_delegate`` nor
  ``disable_general_purpose_subagent`` is called.

Offline: ``_build_deep_agent_for`` is stubbed with an AsyncMock to CAPTURE its
``subagents`` kwarg (so no real deep-agent build), ``stream_deep_agent_events`` yields
one trivial ``agent_done`` frame, and ``build_read_only_delegate`` /
``disable_general_purpose_subagent`` are patched at their source module so no DB tool
resolution or process-global ``_HARNESS_PROFILES`` mutation happens.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from src.orchestrator.prompts import PRESENTER_VOICE
from tests.conftest import make_mock_settings


def _make_invoker(*, deep_delegates_enabled: bool) -> AgentInvoker:
    """Build a real AgentInvoker (runtime=deep) reaching the delegate seam.

    ``cheap_mode=False`` keeps the in-memory Perceiver at its sonnet tier / 6144 thinking
    so the delegate-source assertions are deterministic. ``get_tools_for_agent`` is an
    AsyncMock because the delegate branch resolves the Perceiver's tools with
    ``tools_override=None`` (unlike the lead, short-circuited via ``tools_override=[]``).
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    agent = SubAgent(name="perceiver", prompt="p", model_tier="sonnet", capability_scope=set())

    return AgentInvoker(
        settings=make_mock_settings(
            runtime="deep",
            deep_delegates_enabled=deep_delegates_enabled,
            cheap_mode=False,
        ),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={"perceiver": agent},
        checkpointer_provider=lambda: None,
    )


async def _agent_done_frame(*a, **k):
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


async def _drive(inv: AgentInvoker) -> list[dict]:
    """Drive call_agent_stream to completion with the LEAD tool resolution short-circuited."""
    return [
        f
        async for f in inv.call_agent_stream(
            "perceiver",
            message="hi",
            user_id="u",
            workspace_id="ws",
            tools_override=[],
        )
    ]


async def test_flag_on_wires_perceiver_delegate_and_gp_disable():
    """deep_delegates_enabled=True: the seam builds the read-only Perceiver delegate,
    passes it as ``subagents`` into ``_build_deep_agent_for``, and GP-disables both the
    lead's and the delegate's built model ids (the direct-Anthropic ``claude-sonnet-4-6``,
    NOT a Bedrock id)."""
    inv = _make_invoker(deep_delegates_enabled=True)
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    fake_delegate = {
        "name": "perceiver",
        "description": "Read-only research delegate.",
        "runnable": object(),
    }

    with (
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_frame),
        patch(
            "src.deep_runtime.delegates.build_read_only_delegate",
            new=AsyncMock(return_value=fake_delegate),
        ) as mock_build_delegate,
        patch("src.deep_runtime.delegates.disable_general_purpose_subagent") as mock_disable_gp,
    ):
        frames = await _drive(inv)

    assert any(f["event"] == "agent_done" for f in frames)

    # (1) _build_deep_agent_for received a non-empty subagents list whose delegate is the Perceiver.
    subagents = inv._build_deep_agent_for.call_args.kwargs["subagents"]
    assert isinstance(subagents, list) and len(subagents) == 1
    assert subagents[0]["name"] == "perceiver"

    # (2) GP-disable was called with the direct-Anthropic sonnet id (lead + delegate models),
    # never a Bedrock id (get_model_for_agent) — proving MODEL_TIER_IDS keying.
    assert mock_disable_gp.called
    disabled_ids = [c.args[0] for c in mock_disable_gp.call_args_list]
    assert "claude-sonnet-4-6" in disabled_ids

    # (3) build_read_only_delegate got the in-memory Perceiver config (thinking-preserved
    # source), proven by its name + sonnet tier — NOT a DB-loaded row from self._agents.
    mock_build_delegate.assert_awaited_once()
    cfg = mock_build_delegate.call_args.args[0]
    assert cfg.name == "perceiver"
    assert cfg.model_tier == "sonnet"


async def test_flag_on_delegate_bypasses_presenter_voice():
    """The delegate uses its OWN role prompt — the lead-only Presenter-voice inline-format
    augmentation is NEVER applied to it (no system_prompt override carrying PRESENTER_VOICE)."""
    inv = _make_invoker(deep_delegates_enabled=True)
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    fake_delegate = {"name": "perceiver", "description": "d", "runnable": object()}

    with (
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_frame),
        patch(
            "src.deep_runtime.delegates.build_read_only_delegate",
            new=AsyncMock(return_value=fake_delegate),
        ) as mock_build_delegate,
        patch("src.deep_runtime.delegates.disable_general_purpose_subagent"),
    ):
        await _drive(inv)

    mock_build_delegate.assert_awaited_once()
    # The helper passes NO system_prompt → the delegate falls back to its own role prompt
    # inside build_read_only_delegate. So no Presenter-voice augmentation can reach it.
    sp = mock_build_delegate.call_args.kwargs.get("system_prompt")
    assert "system_prompt" not in mock_build_delegate.call_args.kwargs
    assert sp is None or PRESENTER_VOICE not in sp


async def test_flag_off_no_delegates_byte_identical():
    """deep_delegates_enabled=False: subagents == () reaches _build_deep_agent_for (the
    7B1 byte-identical path), and NEITHER the delegate build NOR the GP-disable fire."""
    inv = _make_invoker(deep_delegates_enabled=False)
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    with (
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _agent_done_frame),
        patch(
            "src.deep_runtime.delegates.build_read_only_delegate",
            new=AsyncMock(),
        ) as mock_build_delegate,
        patch("src.deep_runtime.delegates.disable_general_purpose_subagent") as mock_disable_gp,
    ):
        frames = await _drive(inv)

    assert any(f["event"] == "agent_done" for f in frames)

    subagents = inv._build_deep_agent_for.call_args.kwargs["subagents"]
    assert subagents == ()
    mock_disable_gp.assert_not_called()
    mock_build_delegate.assert_not_called()
