"""Golden characterization tests for the chat-pipeline fold (ORCH-P1-1, Phase 0).

These freeze the CURRENT behavior of ``JarvisOrchestrator.process_message`` (batch,
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

pytestmark = pytest.mark.asyncio

TRACE_ID = "trace_gold"
ILOG_ID = "ilog_gold"
_JARVIS = "src.orchestrator.jarvis"


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
    """Construct a JarvisOrchestrator with every collaborator mocked.

    ``canned`` maps agent_name -> the text that agent "returns". The same map
    drives both the batch ``_call_agent`` and the streaming ``_call_agent_stream``
    so the two paths are exercised against identical agent outputs.
    """
    from src.orchestrator.jarvis import JarvisOrchestrator

    orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
    rec = _Recorder()

    trace = MagicMock()
    trace.trace_id = TRACE_ID
    orch._trace_manager = MagicMock()
    orch._trace_manager.start_trace = MagicMock(return_value=trace)
    orch._trace_manager.finish_trace = AsyncMock()

    orch._client = MagicMock()
    orch._haiku_model = "claude-haiku"
    orch._db_factory = MagicMock()
    orch._interaction_learner = None

    def _spawn_background(coro):
        # _fire_event / learner schedule coroutines; close them so AsyncMock
        # still records the call args but no "never awaited" warning fires.
        if hasattr(coro, "close"):
            coro.close()

    orch._spawn_background = _spawn_background
    orch._load_conversation_history = AsyncMock(return_value="")
    orch._bump_perception_for_sources = AsyncMock()
    orch._emit_runtime_event = AsyncMock()
    orch._publish_event = AsyncMock()
    orch._get_available_capabilities = AsyncMock(return_value=[])
    orch._persist_plan_record = AsyncMock(side_effect=lambda plan, *a, **k: plan)
    orch._log_interaction = AsyncMock(return_value=ILOG_ID)
    orch._handle_system_capability = AsyncMock(return_value="SYS_OK")
    orch._push_presenter_surface = AsyncMock(return_value=None)
    orch._ensure_learner_deps = AsyncMock()

    async def _call_agent(agent_name, *, message, **kw):
        rec.agent_messages.append((agent_name, message))
        return canned.get(agent_name, "")

    async def _call_agent_stream(agent_name, *, message, **kw):
        rec.agent_messages.append((agent_name, message))
        text = canned.get(agent_name, "")
        yield {"event": "agent_start", "agent": agent_name, "model": "m"}
        yield {"event": "text_delta", "agent": agent_name, "text": text}
        yield {"event": "agent_done", "agent": agent_name, "text": text}

    orch._call_agent = _call_agent
    orch._call_agent_stream = _call_agent_stream
    return orch, rec


def _step(step_id, capability, *, actor="jarvis", risk="none", description="do", user_context=None):
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
            f"{_JARVIS}.classify_intent",
            new=AsyncMock(return_value=(intent, confidence, [])),
        ),
        patch(f"{_JARVIS}.extract_plan", new=MagicMock(return_value=plan)),
        patch(f"{_JARVIS}.intent_to_plan", new=MagicMock(return_value=plan)),
        patch(
            f"{_JARVIS}.resolve_plan_routing",
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
    s1 = _step("s1", "email.send", actor="jarvis", risk="high", description="send email")
    s2 = _step(
        "s2",
        "approve.manual",
        actor="user",
        description="confirm send",
        user_context="needs your ok",
    )
    plan = PlanOutput(goal="send", reasoning="r", steps=[s1, s2])
    routing = [(s1, "operator", [{"name": "t"}])]
    canned = {"planner": "PLAN_TEXT", "operator": "SENT", "presenter": "Done."}
    return plan, routing, [s2], canned


class TestUserActions:
    async def test_batch_includes_user_actions(self):
        plan, routing, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
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
            f"{_JARVIS}.classify_intent",
            new=AsyncMock(side_effect=ValueError("boom")),
        ):
            result = await _run_batch(orch)

        assert result["trace_id"] == TRACE_ID
        assert result["decision"] == "error"
        assert set(result) == {"trace_id", "decision", "summary", "code", "correlation_id"}
        orch._emit_runtime_event.assert_awaited()

    async def test_stream_failure_emits_error_event(self):
        orch, _ = _make_orch({})
        with patch(
            f"{_JARVIS}.classify_intent",
            new=AsyncMock(side_effect=ValueError("boom")),
        ):
            stream = await _run_stream(orch)

        err = [e for e in stream if e.get("event") == "error"]
        assert err
        assert "code" in err[-1] and "correlation_id" in err[-1]


# ── Scenario F: drift #4 — plan event name + firing discipline ────────────────


class TestDriftFourPlanEvent:
    """DRIFT #4 (spec §5): batch fires legacy ``plan_generated`` via
    ``_publish_event`` (awaited, agent-stream bus, no durable record); stream
    fires canonical ``plan_created`` via ``_emit_runtime_event`` (background,
    durable runtime-events bus). Frozen so the reconciliation is visible."""

    async def test_batch_fires_plan_generated_not_plan_created(self):
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
        assert "plan_generated" in publish_names
        assert "plan_created" not in runtime_names

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
        routing = [(s1, "operator", [{"name": "t"}])]
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
        # Risky step skipped -> operator never executed.
        assert rec.message_to("operator") is None
