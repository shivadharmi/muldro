"""Step-10A Task 6 (A4): ``AgentInvoker._build_delegate_subagents`` degrades to no
delegates instead of crashing the turn.

Two failure modes hardened:
1. A malformed ``model_tier`` (DB corruption / bad migration) on the lead OR the
   Perceiver delegate config used to ``KeyError`` on the raw ``MODEL_TIER_IDS[...]``
   subscript inside ``disable_general_purpose_subagent(MODEL_TIER_IDS[tier])``. Now
   ``.get(tier, MODEL_TIER_IDS["sonnet"])`` degrades to the sonnet MODEL ID
   (``claude-sonnet-4-6``) instead of raising — a real model id, not the tier NAME
   ``"sonnet"`` (which every consumer — model build, budget pricing, GP-disable harness
   key — would misread).
2. Any exception raised while building the delegate (tool resolution, delegate
   construction, ...) used to propagate and crash the whole deep-agent turn. Now the
   body is wrapped in try/except and degrades to ``[]`` (no delegates) — the lead can
   still serve the turn alone.

Both failure modes are DORMANT in production (``deep_delegates_enabled`` defaults to
False; the live ``legacy`` runtime never calls this method at all), but must not crash
a turn once the flag is flipped on.

Mirrors the ``_make_invoker_with_approval`` idiom from test_agent_invoker_resume.py:
a real ``AgentInvoker`` with mock collaborators (tool_executor, db_factory) built via
``make_mock_settings()``.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from src.deep_runtime.model_factory import MODEL_TIER_IDS
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings


def _make_invoker() -> AgentInvoker:
    """Build a real AgentInvoker with the minimal mocks needed to reach
    ``_build_delegate_subagents``: a tool_executor whose ``get_tools_for_agent``
    resolves (feeds ``_resolve_tools``) and a no-op db_factory (unused by the method
    itself, but required by the real ``build_read_only_delegate``/patched call)."""
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    fake_db = MagicMock(name="fake-db")

    @asynccontextmanager
    async def _db_factory():
        yield fake_db

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", cheap_mode=False),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _db_factory,
        tool_executor=tool_executor,
        context=context,
        agents={},
    )


async def test_malformed_lead_tier_degrades_to_sonnet_id_not_keyerror():
    """A lead with a bogus (malformed) model_tier must not KeyError on the raw
    MODEL_TIER_IDS subscript — .get(tier, MODEL_TIER_IDS["sonnet"]) defaults it to a real
    model id instead, and the delegate build proceeds normally (returns [<delegate>])."""
    lead_agent = SubAgent(name="planner", prompt="p", model_tier="bogus", capability_scope=set())
    sentinel_delegate = {"name": "perceiver-delegate"}

    inv = _make_invoker()

    with (
        patch("src.deep_runtime.delegates.disable_general_purpose_subagent") as mock_disable,
        patch(
            "src.deep_runtime.delegates.build_read_only_delegate",
            AsyncMock(return_value=sentinel_delegate),
        ) as mock_build,
    ):
        result = await inv._build_delegate_subagents(lead_agent, workspace_id="ws", user_id="u")

    assert result == [sentinel_delegate]
    mock_build.assert_awaited_once()
    # The bogus tier degrades to the sonnet MODEL ID (a VALUE in MODEL_TIER_IDS), NOT the
    # tier NAME "sonnet" (a KEY): consumers (build_chat_model, budget pricing, GP-disable
    # harness key) all require a real Anthropic model id, so the fallback must be one.
    fallback = mock_disable.call_args_list[0].args[0]
    assert fallback == MODEL_TIER_IDS["sonnet"] == "claude-sonnet-4-6"
    assert fallback in MODEL_TIER_IDS.values()  # a model id, never a tier key


async def test_delegate_build_failure_degrades_to_empty_list():
    """A real exception raised while building the delegate (e.g. build_read_only_delegate
    blowing up) must not propagate — the method degrades to [] so the lead can still
    serve the turn alone."""
    lead_agent = SubAgent(name="planner", prompt="p", model_tier="sonnet", capability_scope=set())

    inv = _make_invoker()

    with (
        patch("src.deep_runtime.delegates.disable_general_purpose_subagent"),
        patch(
            "src.deep_runtime.delegates.build_read_only_delegate",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await inv._build_delegate_subagents(lead_agent, workspace_id="ws", user_id="u")

    assert result == []
