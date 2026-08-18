"""Golden characterization tests for the chat-pipeline fold (ORCH-P1-1, Phase 0).

These freeze the CURRENT behavior of ``MuldroOrchestrator.process_message`` (batch,
returns a ``result`` dict) and ``process_message_stream`` (SSE, yields event dicts)
*before* the fold reconciles their drift. Per ``docs/engineering-standards.md`` §5,
characterization tests come first so that:

* the behavior commits (spec rows #2, #4) produce an intentional, minimal diff that
  these snapshots make visible, and
* the structural fold (rows #1 prompt-style, #3 direct-answer, #5 output contract,
  #6 mode) is *proven* a no-op by these snapshots staying green.

Source of truth: ``docs/superpowers/specs/2026-06-16-chat-pipeline-fold-spec.md``
§3 (contracts to freeze), §5 (drift table), §6 (plan matrix).

The harness builds the orchestrator via ``__new__`` and injects mocks (the pattern
in ``test_chat_plan_event.py``). Agent calls are plain async fns/generators that
*record the exact message* each agent receives — that recording is what pins the
drift-#2 prior-context divergence. Event emitters are ``AsyncMock`` so the
drift-#4 ``plan_generated`` vs ``plan_created`` split is assertable.
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
    """Captures the ``(agent_name, message)`` pairs both paths feed their agents."""

    def __init__(self) -> None:
        self.agent_messages: list[tuple[str, str]] = []

    def message_to(self, agent_name: str) -> str | None:
        for name, msg in self.agent_messages:
            if name == agent_name:
                return msg
        return None


def _make_orch(canned: dict[str, str]) -> tuple[object, _Recorder]:
    """Construct a ChatProcessor with every collaborator mocked.

    ``canned`` maps agent_name -> the text that agent "returns". The same map
    drives both the batch ``call_agent`` and the streaming ``call_agent_stream``
    so the two paths are exercised against identical agent outputs.

    The post-chat-extraction harness builds the collaborator directly (mirroring
    ``test_chat_plan_event.py``). For assertion ergonomics the recorded runtime/
    publish mocks are also bound to convenience attributes (``_emit_runtime_event``,
    ``_publish_event``, ``_trace_manager``) on the returned instance.
    """
    from src.orchestrator.chat_processor import ChatProcessor

    chat = ChatProcessor.__new__(ChatProcessor)
    rec = _Recorder()

    # deep_single_lead=False (explicit) → the P2.3 effective-mode resolution short-circuits
    # on the cheap flag, keeping these golden scenarios on the legacy path (byte-neutral).
    chat._settings = make_mock_settings(deep_single_lead=False)

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

    async def _call_agent(agent_name, *, message, **kw):
        rec.agent_messages.append((agent_name, message))
        return canned.get(agent_name, "")

    async def _call_agent_stream(agent_name, *, message, **kw):
        rec.agent_messages.append((agent_name, message))
        text = canned.get(agent_name, "")
        yield {"event": "agent_start", "agent": agent_name, "model": "m"}
        yield {"event": "text_delta", "agent": agent_name, "text": text}
        yield {"event": "agent_done", "agent": agent_name, "text": text}

    chat._invoker = MagicMock()
    chat._invoker.call_agent = _call_agent
    chat._invoker.call_agent_stream = _call_agent_stream
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


def _patches(plan: PlanOutput, routing, user_steps, *, intent="compose_request", confidence=0.9):
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
        patch(
            f"{_MULDRO}.resolve_plan_routing",
            new=AsyncMock(return_value=(routing, user_steps)),
        ),
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


# ── Scenario A: single read-only Perceiver step (presenter-skip) ──────────────


def _scenario_single_read():
    plan = PlanOutput(
        goal="check calendar",
        reasoning="user asked",
        steps=[
            _step("s1", "calendar.read", description="read calendar"),
        ],
    )
    routing = [(plan.steps[0], "perceiver", [{"name": "t"}])]
    canned = {
        "planner": "PLAN_TEXT",
        "perceiver": '{"synthesis": "You have 2 meetings today."}',
    }
    return plan, routing, [], canned


class TestSingleReadDirectAnswer:
    """A lone read-only Perceiver step returns its own synthesis and skips the
    Presenter LLM call — identically on both paths (drift #3 converges here)."""

    async def test_batch_returns_synthesis_and_skips_presenter(self):
        plan, routing, users, canned = _scenario_single_read()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, routing, users)
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
        assert "step_0_calendar.read" in result
        # Presenter was NOT invoked (skip path).
        assert rec.message_to("presenter") is None
        # Exact key-set is the public contract returned verbatim by routes_ws.
        assert set(result) == {
            "trace_id",
            "run_id",
            "interaction_id",
            "plan",
            "summary",
            "step_0_calendar.read",
            "presentation",
        }

    async def test_stream_emits_response_and_skips_presenter(self):
        plan, routing, users, canned = _scenario_single_read()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, routing, users)
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
        # perceiver streamed, then a direct response, then done.
        assert "agent_done" in names
        assert names[-1] == "done"
        responses = [e for e in stream if e.get("event") == "response"]
        assert responses and responses[-1]["text"] == "You have 2 meetings today."
        assert rec.message_to("presenter") is None


# ── Scenario B: multi-step read -> read (drift #2: prior-context injection) ────


def _scenario_multi_step():
    s1 = _step("s1", "calendar.read", description="read calendar")
    s2 = _step("s2", "knowledge.search", description="search notes")
    plan = PlanOutput(goal="prep", reasoning="multi", steps=[s1, s2])
    routing = [(s1, "perceiver", [{"name": "t"}]), (s2, "librarian", [{"name": "t"}])]
    canned = {
        "planner": "PLAN_TEXT",
        "perceiver": "CAL_RESULT",
        "librarian": "NOTES_RESULT",
        "presenter": "Here is your prep.",
    }
    return plan, routing, [], canned


class TestDriftTwoPriorContext:
    """DRIFT #2 (spec §5, reconciled 2026-06-16): both paths now inject only the
    narrow ``step_outputs`` (prior agent text) into downstream agents. Batch no
    longer leaks trace_id / interaction_id / plan / summary — it converged onto
    the stream path's behavior."""

    async def test_batch_injects_only_prior_agent_text_no_leak(self):
        plan, routing, users, canned = _scenario_multi_step()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, routing, users)
        for c in ctx:
            c.start()
        try:
            await _run_batch(orch)
        finally:
            for c in ctx:
                c.stop()

        librarian_msg = rec.message_to("librarian")
        assert librarian_msg is not None
        # Reconciled batch behavior: prior agent text only, no metadata leak.
        assert "CAL_RESULT" in librarian_msg
        assert TRACE_ID not in librarian_msg
        assert ILOG_ID not in librarian_msg

    async def test_stream_injects_only_prior_agent_text(self):
        plan, routing, users, canned = _scenario_multi_step()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, routing, users)
        for c in ctx:
            c.start()
        try:
            await _run_stream(orch)
        finally:
            for c in ctx:
                c.stop()

        librarian_msg = rec.message_to("librarian")
        assert librarian_msg is not None
        # Stream's narrow step_outputs: prior agent text only, no metadata.
        assert "CAL_RESULT" in librarian_msg
        assert TRACE_ID not in librarian_msg
        assert ILOG_ID not in librarian_msg


# ── Scenario C: user-action step (user_actions surfaced both ways) ────────────


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
    routing = [(s1, "executor", [{"name": "t"}])]
    canned = {"planner": "PLAN_TEXT", "executor": "SENT", "presenter": "Done."}
    return plan, routing, [s2], canned


class TestUserActions:
    async def test_batch_includes_user_actions_and_skips_risky_step(self):
        # Batch defaults to mode="plan" (drift #6): the HIGH-risk executor step
        # is surfaced for approval, not executed; user actions still surface.
        plan, routing, users, canned = _scenario_user_action()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, routing, users)
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
        # Risky executor step was skipped, surfaced under plan_ready.
        assert rec.message_to("executor") is None
        assert "step_0_email.send" not in result
        assert result["plan_ready"] == [
            {"plan_id": plan.plan_id, "message": "Plan created. Review and approve to execute."}
        ]

    async def test_batch_ask_override_executes_risky_step(self):
        # An interactive caller passing mode="ask" executes the risky step.
        plan, routing, users, canned = _scenario_user_action()
        orch, rec = _make_orch(canned)
        ctx = _patches(plan, routing, users)
        for c in ctx:
            c.start()
        try:
            result = await _run_batch(orch, mode="ask")
        finally:
            for c in ctx:
                c.stop()

        assert rec.message_to("executor") is not None
        assert result["step_0_email.send"] == "SENT"
        assert "plan_ready" not in result

    async def test_stream_emits_user_actions_event(self):
        plan, routing, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, routing, users)
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
    routing = [(s1, "", [])]
    canned = {"planner": "PLAN_TEXT", "presenter": "Acknowledged."}
    return plan, routing, [], canned


class TestSystemStep:
    async def test_batch_stores_system_result(self):
        plan, routing, users, canned = _scenario_system_step()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, routing, users)
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
        plan, routing, users, canned = _scenario_system_step()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, routing, users)
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
        # Batch now folds from the shared core, which fires runtime events in the
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
    """DRIFT #4 (spec §5, reconciled 2026-06-16): both paths now fire the
    canonical durable ``plan_created`` via ``_emit_runtime_event`` (background);
    the legacy ``plan_generated`` ``_publish_event`` (agent-stream bus, no
    consumer) is gone from the batch path."""

    async def test_batch_fires_plan_created_not_plan_generated(self):
        plan, routing, users, canned = _scenario_multi_step()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, routing, users)
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
        plan, routing, users, canned = _scenario_multi_step()
        orch, _ = _make_orch(canned)
        ctx = _patches(plan, routing, users)
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
    async def test_plan_mode_marks_requires_user_input_and_skips_risky(self):
        s1 = _step("s1", "email.send", risk="high", description="send")
        plan = PlanOutput(goal="send", reasoning="r", steps=[s1])
        routing = [(s1, "executor", [{"name": "t"}])]
        canned = {"planner": "PLAN_TEXT", "presenter": "Plan ready."}
        orch, rec = _make_orch(canned)
        # mode="plan"/"execute" forces use_planner True regardless of intent.
        ctx = _patches(plan, routing, [], intent="greeting", confidence=0.99)
        for c in ctx:
            c.start()
        try:
            stream = await _run_stream(orch, mode="plan")
        finally:
            for c in ctx:
                c.stop()

        names = _events(stream)
        assert "plan_ready" in names
        # Risky step skipped -> executor never executed.
        assert rec.message_to("executor") is None
