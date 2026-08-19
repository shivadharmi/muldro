"""Golden tests for the chat pipeline's public contracts.

Originally (ORCH-P1-1, Phase 0) these froze the drift between ``process_message``
(batch, returns a ``result`` dict) and ``process_message_stream`` (SSE, yields event
dicts) so the fold that reconciled them was provably minimal. The single-lead collapse
deleted the machinery half of that drift table: there is no per-step agent loop, so
nothing threads prior-step results between agents (drift #2) and there is no Presenter
step to prompt in two styles (drift #1) or to skip for a single read (drift #3).

What survives here is what both shells still owe their callers: the batch result key
set ``routes_ws`` returns verbatim, the SSE event names the web client switches on, the
failure shape, and the ``plan_created`` runtime event. Every turn runs ONE lead, so the
harness mocks ``build_chat_lead`` / ``stream_deep_lead`` rather than per-agent calls.

The harness builds the orchestrator via ``__new__`` and injects mocks (the pattern in
``test_chat_plan_event.py``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.contracts import PlanOutput, PlanStep
from tests.conftest import make_mock_settings

pytestmark = pytest.mark.asyncio

TRACE_ID = "trace_gold"
ILOG_ID = "ilog_gold"
_MULDRO = "src.orchestrator.chat_processor"


class _Recorder:
    """Captures what each turn fed the Planner and the lead."""

    def __init__(self) -> None:
        self.agent_messages: list[tuple[str, str]] = []
        self.lead_calls: list[dict] = []

    def message_to(self, agent_name: str) -> str | None:
        for name, msg in self.agent_messages:
            if name == agent_name:
                return msg
        return None


def _make_orch(canned: dict[str, str]) -> tuple[object, _Recorder]:
    """Construct a ChatProcessor with every collaborator mocked.

    ``canned`` maps a name -> the text that "returns": ``"planner"`` for the Planner's
    ``call_agent_stream`` text, ``"lead"`` for the text the mocked ``stream_deep_lead``
    emits on ``agent_done``.

    For assertion ergonomics the recorded runtime/publish mocks are also bound to
    convenience attributes (``_emit_runtime_event``, ``_publish_event``,
    ``_trace_manager``) on the returned instance.
    """
    from src.orchestrator.chat_processor import ChatProcessor

    chat = ChatProcessor.__new__(ChatProcessor)
    rec = _Recorder()

    chat._settings = make_mock_settings()

    trace = MagicMock()
    trace.trace_id = TRACE_ID
    chat._trace_manager = MagicMock()
    chat._trace_manager.start_trace = MagicMock(return_value=trace)
    chat._trace_manager.finish_trace = AsyncMock()

    chat._client = MagicMock()
    chat._haiku_model = "claude-haiku"
    chat._db_factory_provider = lambda: MagicMock()
    chat._interaction_learner = None

    def _spawn_background(coro):
        # _fire_event / learner schedule coroutines; close them so AsyncMock
        # still records the call args but no "never awaited" warning fires.
        if hasattr(coro, "close"):
            coro.close()

    chat._spawn_background = _spawn_background
    chat._ensure_learner_deps = AsyncMock()

    # Collaborators. Convenience aliases (_emit_runtime_event/_publish_event/
    # _get_available_capabilities) keep the assertion sites unchanged.
    emit_runtime_event = AsyncMock()
    publish_event = AsyncMock()

    chat._context = MagicMock()
    chat._context.load_conversation_history = AsyncMock(return_value="")
    chat._context.assemble_context = AsyncMock(return_value="")

    chat._perception = MagicMock()
    chat._perception._bump_perception_for_sources = AsyncMock()

    chat._events = MagicMock()
    chat._events.emit_runtime_event = emit_runtime_event
    chat._events.publish_event = publish_event
    chat._emit_runtime_event = emit_runtime_event
    chat._publish_event = publish_event

    chat._get_available_capabilities = AsyncMock(return_value=[])

    chat._plans = MagicMock()
    chat._plans.persist_plan_record = AsyncMock(side_effect=lambda plan, *a, **k: plan)
    chat._plans.log_interaction = AsyncMock(return_value=ILOG_ID)

    chat._system_capability_handler = MagicMock()
    chat._system_capability_handler.handle_system_capability = AsyncMock(return_value="SYS_OK")

    chat._surfaces = MagicMock()
    chat._surfaces.push_presenter_surface = AsyncMock(return_value=None)

    async def _call_agent_stream(agent_name, *, message, **kw):
        rec.agent_messages.append((agent_name, message))
        text = canned.get(agent_name, "")
        yield {"event": "agent_start", "agent": agent_name, "model": "m"}
        yield {"event": "text_delta", "agent": agent_name, "text": text}
        yield {"event": "agent_done", "agent": agent_name, "text": text}

    async def _stream_deep_lead(lead, tools=None, **kw):
        rec.lead_calls.append(kw)
        text = canned.get("lead", "")
        yield {"event": "agent_start", "agent": "lead", "model": "m"}
        yield {"event": "text_delta", "agent": "lead", "text": text}
        yield {"event": "agent_done", "agent": "lead", "text": text}

    fake_lead = MagicMock(name="fake_lead")
    chat._invoker = MagicMock()
    chat._invoker.call_agent_stream = _call_agent_stream
    chat._invoker.build_chat_lead = AsyncMock(return_value=fake_lead)
    chat._invoker.stream_deep_lead = _stream_deep_lead
    chat._invoker.has_durable_checkpointer = MagicMock(return_value=True)
    chat._fake_lead = fake_lead
    return chat, rec


def _step(step_id, capability, *, actor="muldro", risk="none", description="do", user_context=None):
    return PlanStep(
        step_id=step_id,
        description=description,
        capability=capability,
        actor=actor,
        risk=risk,
        user_context=user_context,
    )


def _patches(plan: PlanOutput, user_steps, *, intent="compose_request", confidence=0.9):
    """Patch the module-level pipeline functions for one scenario.

    Returns a context-manager list to enter via ``with``. ``intent`` defaults to
    a non-FAST intent so the Planner path runs (capturing ``plan_text``).
    """
    return [
        patch(
            f"{_MULDRO}.classify_intent",
            new=AsyncMock(return_value=(intent, confidence, [])),
        ),
        patch(f"{_MULDRO}.extract_plan", new=MagicMock(return_value=plan)),
        patch(f"{_MULDRO}.intent_to_plan", new=MagicMock(return_value=plan)),
        patch(f"{_MULDRO}.resolve_plan_routing", new=MagicMock(return_value=user_steps)),
        patch(f"{_MULDRO}.workspace_allows_bypass", new=AsyncMock(return_value=True)),
    ]


async def _run_batch(orch, **kw):
    return await orch.process_message(
        message=kw.pop("message", "hello"),
        user_id="usr_1",
        workspace_id="ws_1",
        **kw,
    )


async def _run_stream(orch, **kw):
    return [
        evt
        async for evt in orch.process_message_stream(
            message=kw.pop("message", "hello"),
            user_id="usr_1",
            workspace_id="ws_1",
            **kw,
        )
    ]


def _events(stream) -> list[str]:
    return [e.get("event") for e in stream]


# ── Scenario A: single read-only step ─────────────────────────────────────────


def _scenario_single_read():
    plan = PlanOutput(
        goal="check calendar",
        reasoning="user asked",
        steps=[
            _step("s1", "calendar.read", description="read calendar"),
        ],
    )
    canned = {"planner": "PLAN_TEXT", "lead": "You have 2 meetings today."}
    return plan, [], canned


class TestSingleReadReply:
    """A lone read still ends in a terminal reply — the lead's own text. The frontend
    has no other source of terminal text, so a turn without it is an empty bubble."""

    async def test_batch_result_key_set(self):
        plan, users, canned = _scenario_single_read()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            result = await _run_batch(orch)
        finally:
            for c in ctx:
                c.stop()

        assert result["presentation"] == "You have 2 meetings today."
        assert result["trace_id"] == TRACE_ID
        assert result["run_id"] is None
        assert result["interaction_id"] == ILOG_ID
        assert result["summary"] == "user asked"
        # Exact key-set is the public contract returned verbatim by routes_ws.
        assert set(result) == {
            "trace_id",
            "run_id",
            "interaction_id",
            "plan",
            "summary",
            "presentation",
        }

    async def test_stream_emits_response(self):
        plan, users, canned = _scenario_single_read()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            stream = await _run_stream(orch)
        finally:
            for c in ctx:
                c.stop()

        names = _events(stream)
        assert names[0] == "trace"
        assert names[1] == "intent"
        assert "plan" in names
        assert "agent_done" in names
        assert names[-1] == "done"
        responses = [e for e in stream if e.get("event") == "response"]
        assert responses and responses[-1]["text"] == "You have 2 meetings today."


# ── Scenario B: multi-step plan ───────────────────────────────────────────────


def _scenario_multi_step():
    s1 = _step("s1", "calendar.read", description="read calendar")
    s2 = _step("s2", "knowledge.search", description="search notes")
    plan = PlanOutput(goal="prep", reasoning="multi", steps=[s1, s2])
    canned = {"planner": "PLAN_TEXT", "lead": "Here is your prep."}
    return plan, [], canned


class TestMultiStepPlan:
    """A multi-step plan is ONE lead call scoped to the plan's capability union — the
    steps are context for the lead, not a routing table."""

    async def test_one_lead_receives_the_whole_plan(self):
        plan, users, canned = _scenario_multi_step()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            result = await _run_batch(orch)
        finally:
            for c in ctx:
                c.stop()

        assert len(rec.lead_calls) == 1
        orch._invoker.build_chat_lead.assert_awaited_once()
        assert orch._invoker.build_chat_lead.await_args.args[0] == plan.steps
        assert result["presentation"] == "Here is your prep."


# ── Scenario C: user-action step ──────────────────────────────────────────────


def _scenario_user_action():
    s1 = _step("s1", "email.send", actor="muldro", risk="high", description="send email")
    s2 = _step(
        "s2",
        "approve.manual",
        actor="user",
        description="confirm send",
        user_context="needs your ok",
    )
    plan = PlanOutput(goal="send", reasoning="r", steps=[s1, s2])
    canned = {"planner": "PLAN_TEXT", "lead": "Done."}
    return plan, [s2], canned


class TestUserActions:
    async def test_batch_includes_user_actions(self):
        plan, users, canned = _scenario_user_action()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            result = await _run_batch(orch)
        finally:
            for c in ctx:
                c.stop()

        assert result["user_actions"] == [
            {"description": "confirm send", "context": "needs your ok"}
        ]

    async def test_stream_emits_user_actions_event(self):
        plan, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            stream = await _run_stream(orch)
        finally:
            for c in ctx:
                c.stop()

        ua = [e for e in stream if e.get("event") == "user_actions"]
        assert ua and ua[0]["steps"] == [
            {"description": "confirm send", "context": "needs your ok"}
        ]


# ── Scenario D: system.* step ─────────────────────────────────────────────────


def _scenario_system_step():
    s1 = _step("s1", "system.respond", description="respond")
    plan = PlanOutput(goal="ack", reasoning="r", steps=[s1])
    canned = {"planner": "PLAN_TEXT", "lead": "Acknowledged."}
    return plan, [], canned


class TestSystemStep:
    async def test_batch_stores_system_result(self):
        plan, users, canned = _scenario_system_step()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            result = await _run_batch(orch)
        finally:
            for c in ctx:
                c.stop()

        # Batch keys the system result into the result dict.
        assert result["system_system.respond"] == "SYS_OK"
        assert result["presentation"] == "Acknowledged."

    async def test_stream_does_not_key_system_result(self):
        plan, users, canned = _scenario_system_step()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            stream = await _run_stream(orch)
        finally:
            for c in ctx:
                c.stop()

        # Stream discards the system result (handled for side effects only).
        assert "agent_done" in _events(stream)


# ── Scenario E: error path (failure contract 3b) ──────────────────────────────


class TestErrorContract:
    async def test_batch_failure_dict_shape(self):
        orch, _ = _make_orch({})
        with patch(
            f"{_MULDRO}.classify_intent",
            new=AsyncMock(side_effect=ValueError("boom")),
        ):
            result = await _run_batch(orch)

        assert result["trace_id"] == TRACE_ID
        assert result["decision"] == "error"
        assert set(result) == {"trace_id", "decision", "summary", "code", "correlation_id"}
        # Batch folds from the shared core, which fires runtime events in the
        # background (drift #4 firing-discipline convergence) — called, not awaited.
        orch._emit_runtime_event.assert_called()
        # The error path must drain the core generator so its finally runs
        # finish_trace deterministically (regression guard for the early-return
        # bug that abandoned the suspended generator).
        orch._trace_manager.finish_trace.assert_awaited_once()

    async def test_stream_failure_emits_error_event(self):
        orch, _ = _make_orch({})
        with patch(
            f"{_MULDRO}.classify_intent",
            new=AsyncMock(side_effect=ValueError("boom")),
        ):
            stream = await _run_stream(orch)

        err = [e for e in stream if e.get("event") == "error"]
        assert err
        assert "code" in err[-1] and "correlation_id" in err[-1]


# ── Scenario F: drift #4 — plan event name + firing discipline ────────────────


class TestDriftFourPlanEvent:
    """DRIFT #4 (spec §5, reconciled 2026-06-16): both paths fire the canonical durable
    ``plan_created`` via ``_emit_runtime_event`` (background); the legacy
    ``plan_generated`` ``_publish_event`` (agent-stream bus, no consumer) is gone."""

    async def test_batch_fires_plan_created_not_plan_generated(self):
        plan, users, canned = _scenario_multi_step()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            await _run_batch(orch)
        finally:
            for c in ctx:
                c.stop()

        publish_names = [c.args[0] for c in orch._publish_event.call_args_list]
        runtime_names = [c.args[0] for c in orch._emit_runtime_event.call_args_list]
        assert "plan_created" in runtime_names
        assert "plan_generated" not in publish_names

    async def test_stream_fires_plan_created_not_plan_generated(self):
        plan, users, canned = _scenario_multi_step()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, users)
        for c in ctx:
            c.start()
        try:
            await _run_stream(orch)
        finally:
            for c in ctx:
                c.stop()

        publish_names = [c.args[0] for c in orch._publish_event.call_args_list]
        runtime_names = [c.args[0] for c in orch._emit_runtime_event.call_args_list]
        assert "plan_created" in runtime_names
        assert "plan_generated" not in publish_names


# ── Scenario G: stream-only `mode` (drift #6, additive) ───────────────────────


class TestStreamMode:
    async def test_plan_mode_marks_requires_user_input(self):
        """``mode="plan"`` marks the plan ``requires_user_input`` and that reaches the
        client on the ``plan`` frame. It no longer decides whether a step runs — writes
        are gated at action time by ``permission_gate`` x ``presence`` instead."""
        s1 = _step("s1", "email.send", risk="high", description="send")
        plan = PlanOutput(goal="send", reasoning="r", steps=[s1])
        canned = {"planner": "PLAN_TEXT", "lead": "Plan ready."}
        orch, rec = _make_orch(canned)
        # mode="plan"/"execute" forces use_planner True regardless of intent.
        ctx = _patches(plan, [], intent="greeting", confidence=0.99)
        for c in ctx:
            c.start()
        try:
            stream = await _run_stream(orch, mode="plan")
        finally:
            for c in ctx:
                c.stop()

        plan_frames = [e for e in stream if e.get("event") == "plan"]
        assert plan_frames and plan_frames[0]["plan"]["requires_user_input"] is True
