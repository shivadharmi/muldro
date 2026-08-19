"""Characterization of the LEGACY multi-agent chat arm's CoreEvent sequence.

TEMPORARY — this file exists so the single-lead collapse is provably behaviour-preserving for
the event sequence the FRONTEND consumes (`routes_chat` translates CoreEvents to SSE and the
UI renders from them). The next task converts each captured sequence into an assertion against
the single-lead path and deletes the legacy half.

Do not extend this file, and do not treat the captured sequences as a specification: they are
a snapshot of what the code did on 2026-08-19, not a statement of what it ought to do. If one
looks wrong, that is a finding to report — not something to correct here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.contracts import PlanOutput
from tests.test_chat_pipeline_golden import (
    _MULDRO,
    _make_orch,
    _patches,
    _run_batch,
    _scenario_multi_step,
    _scenario_single_read,
    _scenario_system_step,
    _scenario_user_action,
    _step,
)

# ── Capture helpers ───────────────────────────────────────────────────────────


async def _stream_event_types(orch, **kw) -> list[str]:
    """The ordered CoreEvent discriminators the STREAM drive mode yields.

    ``process_message_events`` is the typed entry point ``process_message_stream``
    is a thin SSE adapter over, so this is the stream path's own sequence
    (``presence=present``, ``prompt_style="conversational"``).
    """
    return [
        event.type
        async for event in orch.process_message_events(
            message=kw.pop("message", "hello"),
            user_id="usr_1",
            workspace_id="ws_1",
            **kw,
        )
    ]


def _spy_core(orch) -> list[str]:
    """Shadow ``_process_core`` on the instance so the BATCH drive mode's own
    CoreEvent sequence is observable.

    ``process_message`` folds events into a dict rather than yielding them, so the
    batch path has no natural sequence at its public boundary. It also drives the
    core with *different* arguments than the stream path (``presence=absent``,
    ``prompt_style="structured"``, ``mode="plan"``), so its sequence is genuinely
    its own and cannot be inferred from the stream capture. Both halves are pinned:
    this list, and the result dict's key set (the contract ``routes_ws`` returns
    verbatim).
    """
    seen: list[str] = []
    original = orch._process_core

    async def _wrapped(*args, **kwargs):
        async for event in original(*args, **kwargs):
            seen.append(event.type)
            yield event

    orch._process_core = _wrapped
    return seen


class _Scenario:
    """Enter the golden ``_patches`` for one scenario as a context manager."""

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for ctx in self._patches:
            ctx.start()
        return self

    def __exit__(self, *exc):
        for ctx in self._patches:
            ctx.stop()
        return False


# ── Scenario A: single read-only Perceiver step (presenter-skip) ──────────────


class TestSingleReadSequence:
    """Two agent triplets only (Planner, Perceiver): the Presenter LLM call is
    skipped, yet ``presentation`` still fires — carrying the Perceiver's own
    synthesis."""

    async def test_stream(self):
        plan, routing, users, canned = _scenario_single_read()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, routing, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "step_result",
            "presentation",
            "run_completed",
        ]

    async def test_batch(self):
        plan, routing, users, canned = _scenario_single_read()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, routing, users)):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "step_result",
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "presentation",
            "run_id",
            "step_0_calendar.read",
            "summary",
            "trace_id",
        ]


# ── Scenario B: multi-step read -> read (Presenter runs) ──────────────────────


class TestMultiStepSequence:
    async def test_stream(self):
        plan, routing, users, canned = _scenario_multi_step()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, routing, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "step_result",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "step_result",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "presentation",
            "run_completed",
        ]

    async def test_batch(self):
        plan, routing, users, canned = _scenario_multi_step()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, routing, users)):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "step_result",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "step_result",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "presentation",
            "run_id",
            "step_0_calendar.read",
            "step_1_knowledge.search",
            "summary",
            "trace_id",
        ]


# ── Scenario C: user-action step + a HIGH-risk write step ─────────────────────


class TestUserActionSequence:
    async def test_stream(self):
        plan, routing, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, routing, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "step_result",
            "user_actions_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "presentation",
            "run_completed",
        ]

    async def test_batch_plan_mode(self):
        plan, routing, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, routing, users)):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "plan_mode_step_skipped",
            "user_actions_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "plan_ready",
            "presentation",
            "run_id",
            "summary",
            "trace_id",
            "user_actions",
        ]

    async def test_batch_ask_mode_executes_the_risky_step(self):
        plan, routing, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, routing, users)):
            result = await _run_batch(orch, mode="ask")

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "step_result",
            "user_actions_ready",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "presentation",
            "run_id",
            "step_0_email.send",
            "summary",
            "trace_id",
            "user_actions",
        ]


# ── Scenario D: system.* step ─────────────────────────────────────────────────


class TestSystemStepSequence:
    async def test_stream(self):
        plan, routing, users, canned = _scenario_system_step()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, routing, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "system_step_result",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "presentation",
            "run_completed",
        ]

    async def test_batch(self):
        plan, routing, users, canned = _scenario_system_step()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, routing, users)):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "system_step_result",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "presentation",
            "run_id",
            "summary",
            "system_system.respond",
            "trace_id",
        ]


# ── Scenario E: plan mode skips a risky step (stream-only `mode`) ─────────────


def _scenario_plan_mode():
    s1 = _step("s1", "email.send", risk="high", description="send")
    plan = PlanOutput(goal="send", reasoning="r", steps=[s1])
    routing = [(s1, "executor", [{"name": "t"}])]
    canned = {"planner": "PLAN_TEXT", "presenter": "Plan ready."}
    return plan, routing, [], canned


class TestPlanModeSequence:
    async def test_stream(self):
        plan, routing, users, canned = _scenario_plan_mode()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, routing, users, intent="greeting", confidence=0.99)):
            types = await _stream_event_types(orch, mode="plan")

        assert types == [
            "trace_started",
            "intent_classified",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "interaction_logged",
            "plan_ready",
            "plan_mode_step_skipped",
            "agent_started",
            "agent_text_delta",
            "agent_done",
            "presentation",
            "run_completed",
        ]


# ── Scenario F: pipeline failure ──────────────────────────────────────────────


class TestFailureSequence:
    async def test_stream(self):
        orch, _ = _make_orch({})
        with patch(f"{_MULDRO}.classify_intent", new=AsyncMock(side_effect=ValueError("boom"))):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "run_failed",
        ]

    async def test_batch(self):
        orch, _ = _make_orch({})
        types = _spy_core(orch)
        with patch(f"{_MULDRO}.classify_intent", new=AsyncMock(side_effect=ValueError("boom"))):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "run_failed",
        ]
        assert sorted(result) == [
            "code",
            "correlation_id",
            "decision",
            "summary",
            "trace_id",
        ]


# ── Scenario G: input validation rejects before the pipeline ──────────────────


class TestValidationSequence:
    async def test_stream_empty_message(self):
        orch, _ = _make_orch({})
        types = await _stream_event_types(orch, message="   ")

        assert types == [
            "validation_failed",
        ]

    async def test_batch_empty_message(self):
        orch, _ = _make_orch({})
        types = _spy_core(orch)
        result = await _run_batch(orch, message="   ")

        # ASYMMETRIC with the stream: the batch adapter returns its own error dict
        # before ``_process_core`` is entered, so NO CoreEvent is produced at all
        # (the stream adapter yields a typed ``validation_failed`` instead).
        assert types == []
        assert sorted(result) == [
            "error",
        ]
