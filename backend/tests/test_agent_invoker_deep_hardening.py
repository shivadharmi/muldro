"""Step 6A.5 + 6B blast-radius: AgentInvoker.call_agent_stream deep branch wires
shells + central dispatcher + SystemMessage + durable checkpointer + trust gate.

Tests confirm:
- runtime="deep": build_tool_shells, make_jarvis_tool_dispatcher, make_trust_gate_middleware,
  build_system_message, and checkpointer_provider are all wired correctly into
  build_deep_agent via the shared ``_build_deep_agent_for`` helper (Step 6B Task 5).
- The gate is OUTER of the write lock and dispatcher
  (``extra_middleware=(governor_audit, trust_gate, write_lock, dispatcher, librarian_extract)``,
  Step 7B1 P1/P3 + 6C Task 1.2); librarian_extract is an @after_model hook appended last
  (post-turn, dormant). The seam passes ``authorization_source="direct_user_request"`` (dormant
  on live chat), and the SAME minted ``thread_id`` is shared by the gate closure and config.
- ``stream_deep_agent_events`` is called with ``durability="sync"``.
- runtime="legacy": agent_loop is still called and the deep adapter is never touched
  (legacy path byte-behavior-identical after adding checkpointer_provider param).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import SystemMessage

from src.deep_runtime.authorization import AuthorizationSource
from src.deep_runtime.thread_identity import workspace_of_thread_id
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings


def _make_invoker(runtime: str, checkpointer_provider=None, **settings_overrides) -> AgentInvoker:
    """Build a real AgentInvoker with the minimal mocks to reach the runtime branch.

    Extends the pattern from test_agent_invoker_runtime_branch._make_invoker with an
    optional ``checkpointer_provider`` param to test the durable-checkpointer wiring.
    ``tools_override=[]`` short-circuits ``_resolve_tools``. ``assemble_context`` returns
    ``""`` so system_blocks build cleanly. ``settings_overrides`` flow through to
    ``make_mock_settings`` (e.g. ``deep_readback_enabled=True``).
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    agent = SubAgent(name="perceiver", prompt="p", model_tier="sonnet", capability_scope=set())

    return AgentInvoker(
        settings=make_mock_settings(runtime=runtime, **settings_overrides),
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
    """runtime=deep: build_deep_agent receives shells, governor_audit+gate+write_lock+dispatcher
    (in order), SystemMessage, provider saver — and the live seam is dormant + durability="sync".
    """
    sentinel_saver = object()
    sentinel_dispatcher = object()
    sentinel_write_lock = object()
    sentinel_gate = object()
    sentinel_governor = object()
    sentinel_librarian = object()
    sentinel_unavailable = object()
    sentinel_budget = object()
    captured_config: dict = {}

    inv = _make_invoker(runtime="deep", checkpointer_provider=lambda: sentinel_saver)

    async def _fake_adapter(agent, graph_input, config, **k):
        captured_config.update(config)
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
            return_value=sentinel_dispatcher,
        ) as mock_dispatcher,
        patch(
            "src.orchestrator.agent_invoker.make_trust_gate_middleware",
            return_value=sentinel_gate,
        ) as mock_gate,
        patch(
            "src.orchestrator.agent_invoker.make_write_lock_middleware",
            return_value=sentinel_write_lock,
        ) as mock_write_lock,
        patch(
            "src.orchestrator.agent_invoker.make_governor_audit_middleware",
            return_value=sentinel_governor,
        ) as mock_governor,
        patch(
            "src.orchestrator.agent_invoker.make_librarian_extract_middleware",
            return_value=sentinel_librarian,
        ) as mock_librarian,
        patch(
            "src.orchestrator.agent_invoker.make_unavailable_server_middleware",
            return_value=sentinel_unavailable,
        ) as mock_unavailable,
        patch(
            "src.orchestrator.agent_invoker.make_budget_middleware",
            return_value=sentinel_budget,
        ) as mock_budget,
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

    # (b) extra_middleware is EXACTLY (governor_audit, unavailable_server, trust_gate, write_lock,
    # dispatcher, librarian_extract, budget_mw) — audit OUTER-MOST, then the Step 7C
    # unavailable_server breaker (OUTER of the gate so a known-down write is short-circuited before
    # approval), gate next, write lock, dispatcher INNER for the wrap_tool_call chain (Step 7B1 P1 +
    # 6C Task 1.2 + 7C P3); build_deep_agent installs capability_scope ahead of all. read_back is
    # absent (deep_readback_enabled=False). librarian_extract (7B1 P3) + budget_mw (7C P3) are
    # @after_model hooks — their tuple positions are irrelevant to the tool chain (post-turn hooks).
    assert kw["extra_middleware"] == (
        sentinel_governor,
        sentinel_unavailable,
        sentinel_gate,
        sentinel_write_lock,
        sentinel_dispatcher,
        sentinel_librarian,
        sentinel_budget,
    ), (
        "expected (governor, unavailable, gate, lock, dispatcher, librarian, budget) order, got "
        f"{kw.get('extra_middleware')!r}"
    )
    # unavailable_server is built with the closure-bound workspace_id (never LLM-supplied).
    assert mock_unavailable.call_args.kwargs["workspace_id"] == "ws"
    # budget_mw is built with the direct-Anthropic model id for the agent's tier (NOT Bedrock).
    bud_kw = mock_budget.call_args.kwargs
    assert bud_kw["model"] == "claude-sonnet-4-6"
    assert bud_kw["agent_name"] == "perceiver"
    assert bud_kw["workspace_id"] == "ws"

    # (b'''') the librarian_extract is built DORMANT (active=False) so it never double-fires
    # with the still-live InteractionLearner, with closure-bound workspace_id/user_id + an
    # injected async learn adapter (never LLM-supplied).
    lib_kw = mock_librarian.call_args.kwargs
    assert lib_kw["active"] is False
    assert lib_kw["workspace_id"] == "ws"
    assert lib_kw["user_id"] == "u"
    assert callable(lib_kw["learn"])

    # (b'') the write lock is built with the closure-bound workspace_id (never LLM-supplied).
    assert mock_write_lock.call_args.kwargs["workspace_id"] == "ws"

    # (b''') the governor_audit is built with the closure-bound workspace_id + the SHARED
    # per-turn ToolDef resolver (6C #1) — never LLM-supplied, never its own db_factory.
    gov_kw = mock_governor.call_args.kwargs
    assert gov_kw["workspace_id"] == "ws"
    assert callable(gov_kw["resolve_tool_def"])

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

    # (e) the gate is built with authorization_source=direct_user_request — dormant on
    # the live chat seam by design (Step 6B; activates in 6C for other provenance).
    gate_kw = mock_gate.call_args.kwargs
    assert gate_kw["authorization_source"] == AuthorizationSource.DIRECT_USER_REQUEST

    # (f) the SAME minted thread_id is shared by the gate closure and the graph config —
    # required so a paused turn (future gated provenance) is resumable on the right thread.
    assert gate_kw["thread_id"] == captured_config["configurable"]["thread_id"]


async def test_deep_readback_flag_on_inserts_readback_between_write_lock_and_dispatcher():
    """Step 7C: with deep_readback_enabled=True, read_back is spliced INNER of write_lock, OUTER of
    dispatcher — the tuple grows to 8 and index 4 is the read-back (the last policy hop before the
    tool actually runs). The rest of the chain is unchanged."""
    sentinel_dispatcher = object()
    sentinel_write_lock = object()
    sentinel_gate = object()
    sentinel_governor = object()
    sentinel_librarian = object()
    sentinel_unavailable = object()
    sentinel_budget = object()
    sentinel_readback = object()

    inv = _make_invoker(
        runtime="deep",
        checkpointer_provider=lambda: object(),
        deep_readback_enabled=True,
    )

    async def _fake_adapter(agent, graph_input, config, **k):
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
            return_value=sentinel_dispatcher,
        ),
        patch(
            "src.orchestrator.agent_invoker.make_trust_gate_middleware",
            return_value=sentinel_gate,
        ),
        patch(
            "src.orchestrator.agent_invoker.make_write_lock_middleware",
            return_value=sentinel_write_lock,
        ),
        patch(
            "src.orchestrator.agent_invoker.make_governor_audit_middleware",
            return_value=sentinel_governor,
        ),
        patch(
            "src.orchestrator.agent_invoker.make_librarian_extract_middleware",
            return_value=sentinel_librarian,
        ),
        patch(
            "src.orchestrator.agent_invoker.make_unavailable_server_middleware",
            return_value=sentinel_unavailable,
        ),
        patch(
            "src.orchestrator.agent_invoker.make_budget_middleware",
            return_value=sentinel_budget,
        ),
        patch(
            "src.orchestrator.agent_invoker.make_readback_middleware",
            return_value=sentinel_readback,
        ) as mock_readback,
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
    mock_readback.assert_called_once()

    kw = mock_build.call_args.kwargs
    assert kw["extra_middleware"] == (
        sentinel_governor,
        sentinel_unavailable,
        sentinel_gate,
        sentinel_write_lock,
        sentinel_readback,
        sentinel_dispatcher,
        sentinel_librarian,
        sentinel_budget,
    ), f"expected read_back spliced INNER of write_lock, got {kw.get('extra_middleware')!r}"
    assert len(kw["extra_middleware"]) == 8
    assert kw["extra_middleware"][4] is sentinel_readback


async def test_deep_branch_passes_durability_sync_to_stream_adapter():
    """runtime=deep: stream_deep_agent_events is called with durability='sync' (D6) so a
    gate interrupt's checkpoint commits before the approval_needed frame is emitted."""
    inv = _make_invoker(runtime="deep", checkpointer_provider=lambda: object())

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

    mock_adapter = MagicMock(side_effect=_fake_adapter)

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
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", mock_adapter),
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
    assert mock_adapter.call_args.kwargs["durability"] == "sync"


async def test_direct_user_request_deep_turn_is_dormant():
    """Proves the SEAM passes authorization_source="direct_user_request" into the REAL
    trust_gate builder — ``make_trust_gate_middleware`` is spied (``wraps=``) rather than
    replaced with a sentinel, so the actual gate-construction code path runs, unlike the
    other tests in this file which stub it out entirely.

    The gate's actual dormancy behaviour (a direct_user_request source calls no DB / no
    risk assessment when a tool is later invoked) is already unit-proven directly against
    the gate in tests/deep_runtime/test_trust_gate.py::test_direct_user_request_short_circuits.
    This test only needs to prove the seam feeds it the right literal.
    """
    from src.deep_runtime.middleware.trust_gate import (
        make_trust_gate_middleware as real_make_trust_gate_middleware,
    )

    inv = _make_invoker(runtime="deep", checkpointer_provider=lambda: object())

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
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch(
            "src.orchestrator.agent_invoker.make_jarvis_tool_dispatcher",
            return_value=object(),
        ),
        patch(
            "src.orchestrator.agent_invoker.make_trust_gate_middleware",
            wraps=real_make_trust_gate_middleware,
        ) as mock_gate,
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
    mock_gate.assert_called_once()
    assert mock_gate.call_args.kwargs["authorization_source"] == "direct_user_request"


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
        patch(
            "src.orchestrator.agent_invoker.make_trust_gate_middleware",
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


async def test_librarian_learn_closure_adapts_interaction_learner():
    """The seam's ``learn`` closure (Step 7B1 P3) adapts the existing InteractionLearner:
    it constructs it with the SAME ctor deps the live jarvis path uses (settings, db_factory,
    vector_store; redis/event_bus resolve to None via getattr) and calls ``.learn`` with the
    turn's user_id/workspace_id/message/response, intent=None, trace_id=thread_id.

    This is the forced-integration teeth for the wiring: the middleware itself is patched to a
    sentinel so we can capture the REAL closure it was built with, then invoke it directly (no
    live model / deep-agent build needed) and assert the InteractionLearner adapter fires
    correctly. Terminal/intermediate/dormant round gating is proven directly against the
    @after_model body in tests/deep_runtime/test_librarian_extract.py.
    """
    captured: dict = {}

    def _capture_librarian(*, workspace_id, user_id, learn, active):
        captured["learn"] = learn
        captured["active"] = active
        return object()

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

    inv = _make_invoker(runtime="deep", checkpointer_provider=lambda: object())

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch("src.orchestrator.agent_invoker.make_jarvis_tool_dispatcher", return_value=object()),
        patch("src.orchestrator.agent_invoker.make_trust_gate_middleware", return_value=object()),
        patch(
            "src.orchestrator.agent_invoker.make_librarian_extract_middleware",
            side_effect=_capture_librarian,
        ),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter),
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

    assert captured["active"] is False  # dormant by design
    learn = captured["learn"]

    # Invoke the captured closure directly with a patched InteractionLearner.
    with patch("src.services.interaction_learner.InteractionLearner") as mock_learner_cls:
        instance = mock_learner_cls.return_value
        instance.learn = AsyncMock()
        await learn("remember Bob works at Acme", "Noted — Bob @ Acme.")

    # Constructed with the live-path ctor deps (settings + db_factory positional).
    mock_learner_cls.assert_called_once()
    ctor_kwargs = mock_learner_cls.call_args.kwargs
    assert "vector_store" in ctor_kwargs
    assert ctor_kwargs["redis"] is None  # lives in services.extras → getattr miss → None
    assert ctor_kwargs["event_bus"] is None

    # .learn called with the turn scope + intent=None + trace_id=thread_id.
    instance.learn.assert_awaited_once()
    lk = instance.learn.await_args.kwargs
    assert lk["user_id"] == "u"
    assert lk["workspace_id"] == "ws"
    assert lk["user_message"] == "remember Bob works at Acme"
    assert lk["agent_response"] == "Noted — Bob @ Acme."
    assert lk["intent"] is None
    # A6 (Step-10A): the trace_id IS the ws-embedded checkpointer thread_id — assert the
    # embedded workspace round-trips (stronger than the old startswith("chat_") prefix check).
    assert workspace_of_thread_id(lk["trace_id"]) == "ws"
