"""The exact ``CoreEvent`` sequence a chat turn yields, per plan shape.

These sequences were CAPTURED from the legacy multi-agent arm immediately before it was
deleted, then re-asserted here against the single lead that replaced it. That is the point
of the file: the collapse changed how a turn executes, and these pin what the change did
and did not do to the events leaving the pipeline.

That matters because this sequence IS the chat contract the frontend renders.
``routes_chat`` translates each ``CoreEvent`` to SSE and the web client switches on the
result — and it persists the assistant reply only on a ``Presentation``, so a turn that
loses that event produces an EMPTY BUBBLE rather than an error. A change to any sequence
below is a change to what the user sees; make it deliberately, with the frontend diff, not
as a side effect of a refactor.

Two crossings are load-bearing and deliberately NOT tidied: the ``PlanReady`` CoreEvent maps
to SSE ``"plan"``, while ``PlanModeStepSkipped`` maps to SSE ``"plan_ready"``. Renaming
either breaks the client silently — no backend test asserts the SSE names.
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

# The Planner call and the lead call each contribute one agent triplet. Before the
# collapse there was one per routed step PLUS one for the Presenter; now there is
# exactly one lead, so every shape below shows the same two.
_PLANNER_TRIPLET = ["agent_started", "agent_text_delta", "agent_done"]
_LEAD_TRIPLET = ["agent_started", "agent_text_delta", "agent_done"]


async def _stream_event_types(orch, **kw) -> list[str]:
    """The ordered CoreEvent discriminators the STREAM drive mode yields.

    ``process_message_events`` is the typed entry point ``process_message_stream``
    is a thin SSE adapter over, so this is the stream path's own sequence
    (``presence=present``).
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
    ``mode="plan"``), so its sequence is genuinely its own and cannot be inferred
    from the stream capture. Both halves are pinned: this list, and the result
    dict's key set (the contract ``routes_ws`` returns verbatim).
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


# ── Scenario A: single read-only step ─────────────────────────────────────────


class TestSingleReadSequence:
    """One read. Two agent triplets (Planner, lead) and a ``presentation`` carrying the
    lead's own answer — the frontend has no other source of terminal text."""

    async def test_stream(self):
        plan, users, canned = _scenario_single_read()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            *_LEAD_TRIPLET,
            "presentation",
            "run_completed",
        ]

    async def test_batch(self):
        plan, users, canned = _scenario_single_read()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, users)):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            *_LEAD_TRIPLET,
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "presentation",
            "run_id",
            "summary",
            "trace_id",
        ]


# ── Scenario B: multi-step read -> read ───────────────────────────────────────


class TestMultiStepSequence:
    """A two-step plan yields the SAME sequence as a one-step plan: the steps scope the
    lead, they do not each get an agent call."""

    async def test_stream(self):
        plan, users, canned = _scenario_multi_step()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            *_LEAD_TRIPLET,
            "presentation",
            "run_completed",
        ]

    async def test_batch(self):
        plan, users, canned = _scenario_multi_step()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, users)):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            *_LEAD_TRIPLET,
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "presentation",
            "run_id",
            "summary",
            "trace_id",
        ]


# ── Scenario C: user-action step + a HIGH-risk write step ─────────────────────


class TestUserActionSequence:
    """``user_actions_ready`` fires BEFORE the lead runs (the user is told what is theirs
    to do whether or not the lead then pauses)."""

    async def test_stream(self):
        plan, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            "user_actions_ready",
            *_LEAD_TRIPLET,
            "presentation",
            "run_completed",
        ]

    async def test_batch_plan_mode(self):
        plan, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, users)):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            "user_actions_ready",
            *_LEAD_TRIPLET,
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "presentation",
            "run_id",
            "summary",
            "trace_id",
            "user_actions",
        ]

    async def test_batch_ask_mode(self):
        """``mode`` no longer changes the sequence: it stopped deciding whether a risky
        step runs, so ask and plan produce the same events. What gates the write now is
        ``permission_gate`` x ``presence`` at action time, inside the lead."""
        plan, users, canned = _scenario_user_action()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, users)):
            result = await _run_batch(orch, mode="ask")

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            "user_actions_ready",
            *_LEAD_TRIPLET,
            "presentation",
            "run_completed",
        ]
        assert sorted(result) == [
            "interaction_id",
            "plan",
            "presentation",
            "run_id",
            "summary",
            "trace_id",
            "user_actions",
        ]


# ── Scenario D: system.* step ─────────────────────────────────────────────────


class TestSystemStepSequence:
    """``system.*`` steps still run deterministically, ahead of the lead."""

    async def test_stream(self):
        plan, users, canned = _scenario_system_step()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            "system_step_result",
            *_LEAD_TRIPLET,
            "presentation",
            "run_completed",
        ]

    async def test_batch(self):
        plan, users, canned = _scenario_system_step()
        orch, _ = _make_orch(canned)
        types = _spy_core(orch)
        with _Scenario(_patches(plan, users)):
            result = await _run_batch(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            "system_step_result",
            *_LEAD_TRIPLET,
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


# ── Scenario E: a risky step under mode="plan" ────────────────────────────────


def _scenario_plan_mode():
    s1 = _step("s1", "email.send", risk="high", description="send")
    plan = PlanOutput(goal="send", reasoning="r", steps=[s1])
    canned = {"planner": "PLAN_TEXT", "lead": "Plan ready."}
    return plan, [], canned


class TestPlanModeSequence:
    """``mode="plan"`` marks the plan ``requires_user_input`` and nothing else: there is
    no ``plan_mode_step_skipped`` any more, because there is no per-step loop to skip in.
    The write is still gated — by ``permission_gate`` inside the lead, at action time."""

    async def test_stream(self):
        plan, users, canned = _scenario_plan_mode()
        orch, _ = _make_orch(canned)
        with _Scenario(_patches(plan, users, intent="greeting", confidence=0.99)):
            types = await _stream_event_types(orch, mode="plan")

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            *_LEAD_TRIPLET,
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
