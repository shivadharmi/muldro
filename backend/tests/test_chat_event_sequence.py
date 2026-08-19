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
    _run_stream,
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


# ── The terminal-text invariant: never end claiming success with nothing to show ──
#
# The sequences above all run a harness lead that always reaches ``agent_done``, which is
# exactly why the two ways a reply can go missing went unnoticed. One lead now carries BOTH
# the tool work and the reply, so a single failure anywhere loses the answer — where the old
# per-step arm still had a dedicated Presenter call left to speak.


def _lead_stream(frames):
    """Replace the harness lead with one that emits exactly ``frames``."""

    async def _stream(lead, tools=None, **kw):
        for frame in frames:
            yield frame

    return _stream


class TestTerminalTextInvariant:
    async def test_a_lead_stream_that_errors_reports_failure_not_completion(self):
        """``stream_adapter`` sanitizes any upstream exception into an ``error`` frame and
        RETURNS — no ``agent_done``, so no ``Presentation``. The turn must then report
        ``run_failed``: a ``run_completed`` here becomes an SSE ``done`` telling the client
        the turn finished normally, while ``routes_chat`` persisted no assistant message at
        all (its insert is gated on truthy reply text). The user's own message IS persisted
        before streaming, so the visible damage is an unanswered turn on reload — reported
        as success."""
        plan, users, canned = _scenario_single_read()
        orch, _ = _make_orch(canned)
        orch._invoker.stream_deep_lead = _lead_stream(
            [
                {"event": "agent_start", "agent": "lead", "model": "m"},
                {
                    "event": "error",
                    "agent": "lead",
                    "code": "internal_error",
                    "message": "Something went wrong.",
                    "correlation_id": "cid_boom",
                },
            ]
        )
        with _Scenario(_patches(plan, users)):
            types = await _stream_event_types(orch)

        assert types == [
            "trace_started",
            "intent_classified",
            *_PLANNER_TRIPLET,
            "interaction_logged",
            "plan_ready",
            "agent_started",
            "agent_stream",  # the sanitized error frame, passed through verbatim once
            "run_failed",
        ]
        # The two things that must NOT happen: a success claim, or a completion tail.
        assert "run_completed" not in types
        assert "presentation" not in types
        orch._surfaces.push_presenter_surface.assert_not_awaited()

    async def test_error_frame_failure_reuses_the_frames_own_safe_fields(self):
        """The frame's ``code``/``message``/``correlation_id`` are already client-safe, so the
        failure quotes them rather than minting a second, unrelated correlation id."""
        plan, users, canned = _scenario_single_read()
        orch, _ = _make_orch(canned)
        orch._invoker.stream_deep_lead = _lead_stream(
            [
                {
                    "event": "error",
                    "agent": "lead",
                    "code": "internal_error",
                    "message": "Something went wrong.",
                    "correlation_id": "cid_boom",
                }
            ]
        )
        with _Scenario(_patches(plan, users)):
            stream = await _run_stream(orch)

        errors = [e for e in stream if e.get("event") == "error"]
        assert errors[-1]["correlation_id"] == "cid_boom"
        assert errors[-1]["code"] == "internal_error"
        assert "done" not in [e.get("event") for e in stream]

    async def test_a_lead_that_finishes_with_no_text_still_persists_something(self):
        """``agent_done`` with empty text IS a completed turn — but an empty ``Presentation``
        is not persisted (``routes_chat`` gates its insert on truthy text), so the turn would
        have no answer on reload. It gets a plain sentence instead. Nothing is invented: the
        sentence says the lead produced no reply, it does not stand in for one."""
        plan, users, canned = _scenario_single_read()
        orch, _ = _make_orch(canned)
        orch._invoker.stream_deep_lead = _lead_stream(
            [
                {"event": "agent_start", "agent": "lead", "model": "m"},
                {"event": "agent_done", "agent": "lead", "text": ""},
            ]
        )
        with _Scenario(_patches(plan, users)):
            result = await _run_batch(orch)

        # Something persistable, and the turn still completes normally.
        assert result["presentation"]
        assert result["presentation"].strip()
        assert "trace_id" in result and "decision" not in result

    async def test_a_reply_that_is_only_a_surface_block_still_leaves_chat_text(self):
        """Same hole by another route: a reply whose whole body is a fenced surface block
        strips to empty. The surface still pushes; the chat must not be left blank."""
        plan, users, canned = _scenario_single_read()
        orch, _ = _make_orch(canned)
        orch._invoker.stream_deep_lead = _lead_stream(
            [{"event": "agent_done", "agent": "lead", "text": "   "}]
        )
        with _Scenario(_patches(plan, users)):
            result = await _run_batch(orch)

        assert result["presentation"].strip()


# ── The chat boundary for a plan-mode risky write ─────────────────────────────
#
# ``mode="plan"`` used to skip any step with ``risk in ("medium","high")`` inside the per-step
# loop. It no longer decides anything about execution: it forces the Planner and stamps
# ``requires_user_input`` (which nothing in ``src/`` reads). The whole safety burden moved to
# ``permission_gate`` x ``presence``, and the batch entry's ``presence=ABSENT`` is what makes a
# confirmation-worthy write STAGE instead of run.
#
# The test below refuses to stop at "what was handed to the lead". It takes the permission_mode
# and presence the real chat boundary produced and drives them through the REAL policy
# functions (``permission_should_interrupt`` + ``resolve_confirmation``, in the order
# ``permission_gate`` calls them), so it goes red if the boundary starts handing over
# ``present``/``bypass`` OR if the policy itself loosens.


class TestPlanModeRiskyWriteBoundary:
    async def test_batch_plan_mode_high_risk_write_is_prepared_not_executed(self):
        from src.deep_runtime.confirmation import resolve_confirmation
        from src.deep_runtime.middleware.permission_gate import permission_should_interrupt
        from src.services.risk_assessor import RiskAssessment

        # A high-risk, irreversible, external write — the shape legacy plan-mode refused to run.
        assessment = RiskAssessment(
            risk_level="high",
            reasoning="sends mail to a third party",
            reversible=False,
            blast_radius="external_single",
        )
        executed: list[str] = []
        prepared: list[str] = []

        plan, users, canned = _scenario_user_action()  # carries a high-risk email.send step
        orch, _ = _make_orch(canned)

        async def _gated_lead(lead, tools=None, **kw):
            """Stand-in lead that runs the REAL gate policy on the REAL boundary values."""
            mode, presence = kw["permission_mode"], kw["presence"]
            if not permission_should_interrupt(mode, assessment):
                executed.append("email.send")
            elif resolve_confirmation(presence) == "prepare":
                prepared.append("email.send")
            else:
                raise AssertionError("interrupted a turn with nobody on it")
            yield {"event": "agent_done", "agent": "lead", "text": "Staged the email."}

        orch._invoker.stream_deep_lead = _gated_lead

        with _Scenario(_patches(plan, users)):
            result = await _run_batch(orch)  # batch default: mode="plan"

        assert executed == []  # THE claim: no ungated external write on a batch turn
        assert prepared == ["email.send"]
        # ...and the turn still finishes and speaks, rather than stalling on a confirmation
        # nobody is there to answer.
        assert result["presentation"] == "Staged the email."

    async def test_batch_plan_mode_hands_the_lead_absent_and_never_bypass(self):
        """The boundary half of the claim above, pinned on its own so a regression names
        itself: ``mode="plan"`` grants nothing, and the batch entry is always ``absent``."""
        plan, users, canned = _scenario_user_action()
        orch, rec = _make_orch(canned)

        with _Scenario(_patches(plan, users)):
            await _run_batch(orch)

        assert len(rec.lead_calls) == 1
        assert rec.lead_calls[0]["presence"] == "absent"
        assert rec.lead_calls[0]["permission_mode"] == "auto"

    def test_medium_risk_reversible_internal_write_now_executes_in_auto(self):
        """A stated behaviour change, pinned rather than left to be discovered: legacy
        plan-mode skipped EVERY medium-or-high-risk step. ``auto`` assesses the real call
        instead of the Planner's coarse label, and a medium-risk write that is reversible with
        an internal blast radius now EXECUTES. Bounded to internal-state mutations."""
        from src.deep_runtime.middleware.permission_gate import permission_should_interrupt
        from src.services.risk_assessor import RiskAssessment

        medium_internal = RiskAssessment(
            risk_level="medium",
            reasoning="updates a record in our own workspace",
            reversible=True,
            blast_radius="internal",
        )
        assert permission_should_interrupt("auto", medium_internal) is False
        # The same call under the old coarse rule would have been skipped; and anything
        # irreversible, external, or high still confirms.
        assert (
            permission_should_interrupt(
                "auto", medium_internal.model_copy(update={"reversible": False})
            )
            is True
        )
        assert (
            permission_should_interrupt(
                "auto", medium_internal.model_copy(update={"blast_radius": "external_single"})
            )
            is True
        )
