"""A scheduled turn must not wear the founder's identity to get past the gate.

``ChatProcessor.process_message`` is the batch entry point, and none of its four production
callers is the founder typing: three scheduler dispatch actions (``meeting_prep``,
``custom_agent_task``, ``wake_agent``) and the WS unknown-action fallback. All four used to
run under the default ``authorization_source=direct_user_request`` — the one literal that
makes ``trust_gate`` short-circuit, on the reasoning that *the user's message IS the
authorization for that turn*. A cron tick wrote one of those messages; a model-authored
``[Action: ...]`` string wrote another.

These tests pin the correction. Each of the four sites DECLARES ``AUTONOMOUS`` (asserted on
the call against a signature-enforcing double — see ``_orchestrator_double``), chat's own
default is unchanged, and the declaration actually REACHES the gate build, where
``is_gated_source`` turns it into a real trust x risk evaluation. Because those turns also run
``presence=absent``, an ``approval_required`` verdict becomes PREPARE rather than an interrupt
into a void.

What that does NOT mean — the scope of the change, stated precisely because an earlier version
of this docstring overclaimed it. Scheduled turns leave ``permission_mode`` at ``"auto"``, and
``resolve_effective_permission_mode("auto", absent, ...)`` returns ``"auto"``, so
``permission_gate`` is installed alongside ``trust_gate``. The chain runs
``trust_gate -> permission_gate`` (trust OUTER), and ``trust_gate``'s auto-execute verdict
returns ``await handler(request)`` — i.e. it falls THROUGH to ``permission_gate``, which
decides on ``mode x assessment`` alone and never consults trust. So a scheduled write that is
irreversible, externally/publicly scoped, or high risk is staged for review at EVERY trust
level, including ``autonomous``; ``email.send`` on a scheduled turn is prepared forever.

Graduation-to-silent therefore applies only to what ``trust_gate`` ALONE would have gated —
reversible, self-scoped, not-high-risk writes — never to what ``permission_gate`` catches.
That is a deliberate division of labour rather than an oversight: ``trust_gate`` asks a
per-CAPABILITY question ("has the founder approved this capability enough times to stop
asking?"), while ``permission_gate`` asks a per-ACTION one ("is THIS write irreversible or
externally visible?"). Letting accrued trust suppress the second would let twenty-five
approved self-scoped sends silently authorize a send to a brand-new external counterparty.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from src.contracts import PlanOutput, PlanStep
from src.deep_runtime.authorization import AuthorizationSource, is_gated_source
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.orchestrator.muldro import MuldroOrchestrator
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

_CP = "src.orchestrator.chat_processor"
_AI = "src.orchestrator.agent_invoker"


# --------------------------------------------------------------------------------------
# 1. Why this task exists, in one line.
# --------------------------------------------------------------------------------------


def test_autonomous_is_a_gated_source():
    """AUTONOMOUS is gated; DIRECT_USER_REQUEST is the one literal that is not.

    Everything below is downstream of this asymmetry: declaring AUTONOMOUS on a turn is
    exactly what stops ``trust_gate`` taking its dormant short-circuit.
    """
    assert is_gated_source(AuthorizationSource.AUTONOMOUS) is True
    assert is_gated_source(AuthorizationSource.DIRECT_USER_REQUEST) is False


# --------------------------------------------------------------------------------------
# 2. Chat's own default is a no-op.
# --------------------------------------------------------------------------------------


def test_process_message_defaults_to_direct_user_request():
    """The default must stay chat's own value — a future edit that flips it fails HERE.

    Every non-batch caller (routes_chat, the WS chat action) relies on the default, so
    flipping it would silently gate real chat, where the founder's message IS the
    authorization.
    """
    from src.orchestrator.chat_processor import ChatProcessor

    param = inspect.signature(ChatProcessor.process_message).parameters["authorization_source"]
    assert param.default == AuthorizationSource.DIRECT_USER_REQUEST


# --------------------------------------------------------------------------------------
# 2b. The facade every production caller actually holds.
# --------------------------------------------------------------------------------------


def test_facade_process_message_matches_chat_processor_signature():
    """``MuldroOrchestrator.process_message`` must accept everything ``ChatProcessor`` does.

    The scheduler and the WS fallback hold a ``MuldroOrchestrator``, never a
    ``ChatProcessor``. When ``authorization_source`` was added to the inner method and not
    the facade, all four production call sites raised ``TypeError`` on every fire — and
    ``_fire``'s caller swallows that into ``consecutive_failures``, which auto-disables the
    schedule after five ticks. Scheduled automation would simply have stopped, silently.

    Pinned as PARITY rather than as one named parameter, so the next parameter added to
    ``ChatProcessor.process_message`` and forgotten here fails the same way.
    """
    from src.orchestrator.chat_processor import ChatProcessor

    def _params(fn):
        return {n: p.default for n, p in inspect.signature(fn).parameters.items() if n != "self"}

    facade = _params(MuldroOrchestrator.process_message)
    inner = _params(ChatProcessor.process_message)

    missing = sorted(set(inner) - set(facade))
    assert not missing, f"facade cannot receive {missing} — production callers will TypeError"

    drifted = {k: (facade[k], inner[k]) for k in inner if facade[k] != inner[k]}
    assert not drifted, f"facade defaults drifted from ChatProcessor: {drifted}"


def test_facade_forwards_authorization_source_to_chat_processor():
    """Accepting the parameter is not enough — the facade must PASS IT ON.

    A signature-only check would still pass if the facade took the argument and dropped it
    on the floor, which fails in the quietest possible way: no error, and every scheduled
    write silently back to wearing the founder's identity.
    """
    orch = MuldroOrchestrator.__new__(MuldroOrchestrator)
    chat = MagicMock()
    chat.process_message = AsyncMock(return_value={})
    orch._chat = chat

    async def _go():
        await orch.process_message(
            message="m",
            user_id="u",
            workspace_id="w",
            authorization_source=AuthorizationSource.AUTONOMOUS,
        )

    asyncio.run(_go())

    assert (
        chat.process_message.await_args.kwargs["authorization_source"]
        == AuthorizationSource.AUTONOMOUS
    )


# --------------------------------------------------------------------------------------
# 3. One test per call site — asserted on the CALL, never on a downstream effect.
# --------------------------------------------------------------------------------------


def _make_schedule(**overrides):
    """Factory for mock Schedule objects (mirrors tests/test_scheduler.py)."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        schedule_id="sched_prov_001",
        user_id=TEST_USER_ID,
        name="test-schedule",
        schedule_type="recurring",
        cron_expr="*/15 * * * *",
        run_at=None,
        action_type="meeting_prep",
        action_config={},
        enabled=True,
        last_run_at=None,
        next_run_at=now - timedelta(minutes=1),
        run_count=5,
        consecutive_failures=0,
        last_error=None,
        source="system",
        priority="medium",
    )
    defaults.update(overrides)
    sched = MagicMock()
    for k, v in defaults.items():
        setattr(sched, k, v)
    return sched


def _orchestrator_double():
    """An orchestrator double that ENFORCES the real ``MuldroOrchestrator`` signature.

    A bare ``MagicMock()`` accepts any kwarg whatsoever, so a call site declaring a kwarg the
    production facade does not accept passes its test and raises ``TypeError`` in production.
    That is exactly how the missing facade parameter shipped: these tests asserted the
    declaration against a receiver that could not receive it. ``create_autospec`` binds the
    real signature, so the same mistake now fails HERE.
    """
    orch = create_autospec(MuldroOrchestrator, instance=True)
    orch.process_message.return_value = {"status": "ok"}
    return orch


def _call_kwargs(mock_method) -> dict:
    """Normalize one recorded call to ``{param_name: value}`` against the REAL signature.

    Binding through the production signature (rather than reading ``call_args.kwargs``) means
    these assertions survive a call site that switches a kwarg to a positional argument.
    """
    assert mock_method.await_count == 1, f"expected one call, got {mock_method.await_count}"
    call = mock_method.await_args
    bound = inspect.signature(MuldroOrchestrator.process_message).bind_partial(
        None, *call.args, **call.kwargs
    )
    bound.apply_defaults()
    return {k: v for k, v in bound.arguments.items() if k != "self"}


async def _fire(action_type: str, action_config: dict) -> dict:
    """Fire one scheduler dispatch action and return the process_message kwargs it used."""
    from src.services.scheduler import SchedulerLoop

    orch = _orchestrator_double()
    scheduler = SchedulerLoop(make_mock_settings(), orchestrator=orch)
    # The workspace lookup is a real DB round-trip that has nothing to do with provenance.
    scheduler._resolve_workspace = AsyncMock(return_value=TEST_WORKSPACE_ID)
    await scheduler._fire(_make_schedule(action_type=action_type, action_config=action_config))
    return _call_kwargs(orch.process_message)


class TestScheduledTurnsDeclareAutonomous:
    """The cron tick authorizes the TURN, not each write inside it."""

    async def test_meeting_prep_declares_autonomous(self):
        kwargs = await _fire("meeting_prep", {})
        assert kwargs["authorization_source"] == AuthorizationSource.AUTONOMOUS

    async def test_custom_agent_task_declares_autonomous(self):
        """The founder authorized the INSTRUCTIONS when creating the schedule; `mode="execute"`
        no longer decides whether a risky step runs, so the writes those instructions imply are
        gated at action time like any other autonomous work."""
        kwargs = await _fire("custom_agent_task", {"instructions": "Review open PRs"})
        assert kwargs["authorization_source"] == AuthorizationSource.AUTONOMOUS
        # The mode override is untouched — this task changed provenance, not mode.
        assert kwargs["mode"] == "execute"

    async def test_wake_agent_declares_autonomous(self):
        """The non-perceiver branch — the perceiver+source branch never reaches
        ``process_message`` at all (it requests a perception run instead)."""
        kwargs = await _fire("wake_agent", {"agent": "librarian", "message": "wake up"})
        assert kwargs["authorization_source"] == AuthorizationSource.AUTONOMOUS


class TestWsFallbackDeclaresAutonomous:
    """The click authorizes the TURN, not each write inside it."""

    async def test_orchestrator_action_fallback_declares_autonomous(self):
        from src.api import routes_ws

        orch = _orchestrator_double()
        app = MagicMock()
        app.state.orchestrator = orch

        db = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock(return_value=ctx)

        with (
            patch("src.models.database.get_session_factory", return_value=factory),
            patch("src.api.deps.resolve_workspace_id", new=AsyncMock(return_value="ws_1")),
        ):
            result = await routes_ws._handle_orchestrator_action(
                "usr_1", "some_unhandled_action", {"context": "ctx"}, app
            )

        assert result["status"] == "success"
        kwargs = _call_kwargs(orch.process_message)
        assert kwargs["authorization_source"] == AuthorizationSource.AUTONOMOUS


# --------------------------------------------------------------------------------------
# 4. The declaration actually REACHES the gate.
# --------------------------------------------------------------------------------------


def _lead() -> SubAgent:
    return SubAgent(
        name="lead",
        prompt="LEAD ROLE",
        model_tier="sonnet",
        capability_scope={"knowledge.search"},
        thinking=ThinkingConfig(enabled=True, budget_tokens=4096),
    )


def _real_invoker(build_calls: list[dict]):
    """A REAL AgentInvoker with only the seams around ``stream_deep_lead`` stubbed, so the
    method's OWN authorization-source threading is exercised rather than mocked away."""
    from src.orchestrator.agent_invoker import AgentInvoker

    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")
    inv = AgentInvoker(
        settings=make_mock_settings(),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=context,
        agents={},
    )

    async def _capture(agent, tools, **kwargs):
        build_calls.append(kwargs)
        return MagicMock(name="deep_agent")

    async def _planner_stream(agent_name, **kw):
        # The Planner is not the subject: it is stubbed so ONLY the lead's build is captured.
        yield {"event": "agent_done", "agent": agent_name, "text": "{}"}

    inv._build_deep_agent_for = _capture
    inv._resolve_tools = AsyncMock(return_value=[])
    inv._resolved_model_id = AsyncMock(return_value="model-x")
    inv.build_chat_lead = AsyncMock(return_value=_lead())
    inv.call_agent_stream = _planner_stream
    inv.has_durable_checkpointer = MagicMock(return_value=True)
    return inv


def _chat_with(invoker):
    """A ChatProcessor whose collaborators are mocked EXCEPT the invoker."""
    from src.orchestrator.chat_processor import ChatProcessor

    chat = ChatProcessor.__new__(ChatProcessor)
    chat._settings = make_mock_settings()
    # MagicMock-truthy hazard: this flag decides which branch _process_core takes, so it is
    # set explicitly rather than inherited from a mock attribute that is truthy by accident.
    chat._settings.chat_planless = False

    trace = MagicMock()
    trace.trace_id = "trace_prov"
    chat._trace_manager = MagicMock()
    chat._trace_manager.start_trace = MagicMock(return_value=trace)
    chat._trace_manager.finish_trace = AsyncMock()

    chat._client = MagicMock()
    chat._haiku_model = "claude-haiku"
    chat._db_factory_provider = lambda: MagicMock()
    chat._interaction_learner = None

    def _spawn_background(coro):
        if hasattr(coro, "close"):
            coro.close()

    chat._spawn_background = _spawn_background
    chat._ensure_learner_deps = AsyncMock()

    chat._context = MagicMock()
    chat._context.load_conversation_history = AsyncMock(return_value="")
    chat._context.assemble_context = AsyncMock(return_value="LEAD_CTX")

    chat._perception = MagicMock()
    chat._perception._bump_perception_for_sources = AsyncMock()
    chat._events = MagicMock()
    chat._events.emit_runtime_event = AsyncMock()
    chat._get_available_capabilities = AsyncMock(return_value=[])

    chat._plans = MagicMock()
    chat._plans.persist_plan_record = AsyncMock(side_effect=lambda plan, *a, **k: plan)
    chat._plans.log_interaction = AsyncMock(return_value="ilog_prov")

    chat._system_capability_handler = MagicMock()
    chat._system_capability_handler.handle_system_capability = AsyncMock(return_value="SYS_OK")
    chat._surfaces = MagicMock()
    chat._surfaces.push_presenter_surface = AsyncMock(return_value=None)

    chat._invoker = invoker
    return chat


def _read_only_plan() -> PlanOutput:
    return PlanOutput(
        goal="look something up",
        reasoning="read only",
        steps=[
            PlanStep(
                step_id="s1",
                description="search",
                capability="knowledge.search",
                actor="muldro",
                risk="none",
            )
        ],
    )


def _agent_done_frames(*a, **k):
    async def _gen():
        yield {"event": "agent_done", "agent": "lead", "text": "done", "tools_called": []}

    return _gen()


async def _drive(authorization_source) -> dict:
    """Run one whole batch turn and return the kwargs ``_build_deep_agent_for`` received."""
    build_calls: list[dict] = []
    chat = _chat_with(_real_invoker(build_calls))
    plan = _read_only_plan()

    kwargs = {}
    if authorization_source is not None:
        kwargs["authorization_source"] = authorization_source

    with (
        patch(f"{_CP}.classify_intent", new=AsyncMock(return_value=("compose_request", 0.9, []))),
        patch(f"{_CP}.extract_plan", new=MagicMock(return_value=plan)),
        patch(f"{_CP}.resolve_plan_routing", new=MagicMock(return_value=[])),
        patch(f"{_CP}.workspace_allows_bypass", new=AsyncMock(return_value=False)),
        patch(f"{_AI}.stream_deep_agent_events", _agent_done_frames),
        patch(f"{_AI}.reap_thread", new=AsyncMock()),
    ):
        await chat.process_message(
            message="hello",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            **kwargs,
        )

    assert len(build_calls) == 1, f"expected one deep-agent build, got {len(build_calls)}"
    return build_calls[0]


class TestProvenanceReachesTheGate:
    async def test_an_autonomous_turn_reaches_the_gate_ungated_no_more(self):
        """THE point of the task: a turn declared AUTONOMOUS builds its deep agent with a
        GATED provenance, so ``trust_gate`` cannot take its dormant short-circuit.

        Driven end-to-end through ``process_message`` -> ``_process_core`` ->
        ``_run_single_lead`` -> ``_stream_lead_and_complete`` -> ``stream_deep_lead``, with only
        ``_build_deep_agent_for`` captured — so every threading hop is real code, and a hop that
        drops the value (e.g. re-hardcoding it in ``stream_deep_lead``) fails here.
        """
        built = await _drive(AuthorizationSource.AUTONOMOUS)

        assert built["authorization_source"] == AuthorizationSource.AUTONOMOUS
        assert is_gated_source(built["authorization_source"]) is True
        # The other half of why a gated write becomes PREPARE rather than an interrupt into
        # a void: nobody is on a batch turn to answer a confirmation.
        assert built["presence"] == "absent"

    async def test_a_chat_turn_still_reaches_the_gate_dormant(self):
        """The complement, so the test above cannot pass by gating everything: the default
        (real chat) still arrives as DIRECT_USER_REQUEST and the gate stays dormant."""
        built = await _drive(None)

        assert built["authorization_source"] == AuthorizationSource.DIRECT_USER_REQUEST
        assert is_gated_source(built["authorization_source"]) is False
