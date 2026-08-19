"""Tests for the chat lead + ``permission_mode`` plumbing.

Every chat turn runs ONE lead. These pin what that means:

* **plumbing** — ``permission_mode`` is a NEW, INDEPENDENT field defaulting to a
  non-bypass value (``"auto"``), threaded through the facades/adapters, NEVER derived
  from the legacy ``mode`` slot.
* **the lead** — ``_process_core`` runs the plan's ``system.*`` steps deterministically,
  reports the user-actor steps, then streams ONE lead whose reply is re-homed as a
  ``Presentation``.
* **Security** — the two authority inputs are ``permission_mode`` and ``presence``, and
  each may only ever DOWNGRADE. The legacy ``mode`` (ask/plan/execute) grants nothing;
  ``bypass`` needs a workspace entitlement AND a present user; and a turn with no durable
  checkpointer is treated as ``absent``, so a gated write is PREPARED rather than
  interrupted into a thread nothing can re-enter.

Harness modeled on ``tests/test_chat_pipeline_golden.py`` (``ChatProcessor.__new__`` +
mocked collaborators). Reply coverage asserts EVERY shape yields a ``Presentation``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.contracts import PlanOutput, PlanStep
from tests.conftest import make_mock_settings

pytestmark = pytest.mark.asyncio

TRACE_ID = "trace_lead"
ILOG_ID = "ilog_lead"
# _MOD: the intent/plan/route/entitlement seams that stay in ``chat_processor`` (the top
# of ``_process_core``). _LEAD: the single-lead BRANCH + shared completion tail moved to
# the ``_ChatSingleLeadMixin`` module (P2.2c) — its surface seams resolve THERE.
_MOD = "src.orchestrator.chat_processor"
_LEAD = "src.orchestrator.chat_single_lead"


class _Recorder:
    """Captures the ``(agent_name, message)`` pairs fed to per-agent calls (only the
    Planner makes one), plus the ``stream_deep_lead`` call kwargs for the lead."""

    def __init__(self) -> None:
        self.agent_messages: list[tuple[str, str]] = []
        self.lead_calls: list[dict] = []

    def called_agent(self, agent_name: str) -> bool:
        return any(name == agent_name for name, _ in self.agent_messages)


def _step(step_id, capability, *, actor="muldro", risk="none", description="do", user_context=None):
    return PlanStep(
        step_id=step_id,
        description=description,
        capability=capability,
        actor=actor,
        risk=risk,
        user_context=user_context,
    )


def _make_chat(
    *,
    lead_text: str = "LEAD_REPLY",
    settings_overrides: dict | None = None,
    durable: bool = True,
) -> tuple[object, _Recorder]:
    """Construct a ChatProcessor with every collaborator mocked.

    ``lead_text`` is the text the mocked ``stream_deep_lead`` emits on ``agent_done``.
    ``settings_overrides`` is merged into ``make_mock_settings``. ``durable`` is what
    ``has_durable_checkpointer()`` returns (True by default so a present turn stays
    present; set False to exercise the presence downgrade).
    """
    from src.orchestrator.chat_processor import ChatProcessor

    chat = ChatProcessor.__new__(ChatProcessor)
    rec = _Recorder()

    chat._settings = make_mock_settings(**(settings_overrides or {}))

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
    chat._plans.log_interaction = AsyncMock(return_value=ILOG_ID)

    chat._system_capability_handler = MagicMock()
    chat._system_capability_handler.handle_system_capability = AsyncMock(return_value="SYS_OK")

    chat._surfaces = MagicMock()
    chat._surfaces.push_presenter_surface = AsyncMock(return_value=None)

    # Per-agent call — only the Planner makes one. Recorded so a test can assert that no
    # OTHER agent was invoked (there is no per-step routing left to invoke one).
    async def _call_agent_stream(agent_name, *, message, **kw):
        rec.agent_messages.append((agent_name, message))
        text = f"{agent_name}_out"
        yield {"event": "agent_start", "agent": agent_name, "model": "m"}
        yield {"event": "agent_done", "agent": agent_name, "text": text}

    # SINGLE-LEAD deep stream — records call kwargs (incl. permission_mode), emits an
    # agent_done frame.
    async def _stream_deep_lead(
        lead,
        tools=None,
        *,
        message,
        context_block,
        user_id,
        workspace_id,
        intent=None,
        trace=None,
        permission_mode=None,
        presence=None,
    ):
        rec.lead_calls.append(
            {
                "lead": lead,
                "tools": tools,
                "message": message,
                "context_block": context_block,
                "user_id": user_id,
                "workspace_id": workspace_id,
                "intent": intent,
                "trace": trace,
                "permission_mode": permission_mode,
                "presence": presence,
            }
        )
        yield {"event": "agent_start", "agent": "lead", "model": "m"}
        yield {"event": "text_delta", "agent": "lead", "text": lead_text}
        yield {"event": "agent_done", "agent": "lead", "text": lead_text}

    fake_lead = MagicMock(name="fake_lead")

    chat._invoker = MagicMock()
    chat._invoker.call_agent_stream = _call_agent_stream
    chat._invoker.build_chat_lead = AsyncMock(return_value=fake_lead)
    chat._invoker.stream_deep_lead = _stream_deep_lead
    # Durable-checkpointer precondition (sync method). True by default so a present turn
    # stays present in the happy-path tests.
    chat._invoker.has_durable_checkpointer = MagicMock(return_value=durable)
    chat._fake_lead = fake_lead
    return chat, rec


def _patches(
    plan: PlanOutput,
    user_steps,
    *,
    intent="compose_request",
    confidence=0.9,
    allow_bypass: bool = True,
):
    """Patch the chat_processor module seams. ``allow_bypass`` is what the entitlement
    helper (``workspace_allows_bypass``) resolves to — True (entitled) by default so
    bypass turns stay in bypass; set False to exercise the bypass→auto downgrade."""
    return [
        patch(f"{_MOD}.classify_intent", new=AsyncMock(return_value=(intent, confidence, []))),
        patch(f"{_MOD}.extract_plan", new=MagicMock(return_value=plan)),
        patch(f"{_MOD}.intent_to_plan", new=MagicMock(return_value=plan)),
        patch(f"{_MOD}.resolve_plan_routing", new=MagicMock(return_value=user_steps)),
        patch(f"{_MOD}.workspace_allows_bypass", new=AsyncMock(return_value=allow_bypass)),
    ]


async def _run_stream(chat, **kw):
    return [
        evt
        async for evt in chat.process_message_stream(
            message=kw.pop("message", "hello"),
            user_id="usr_1",
            workspace_id="ws_1",
            **kw,
        )
    ]


async def _run_batch(chat, **kw):
    return await chat.process_message(
        message=kw.pop("message", "hello"),
        user_id="usr_1",
        workspace_id="ws_1",
        **kw,
    )


def _events(stream) -> list[str]:
    return [e.get("event") for e in stream]


def _responses(stream) -> list[str]:
    return [e["text"] for e in stream if e.get("event") == "response"]


# ── Reply coverage: every plan shape yields exactly one Presentation via the lead ──


_SHAPES = {
    "single_read": [_step("s1", "calendar.read", description="read cal")],
    "knowledge_only": [_step("s1", "knowledge.search", description="search notes")],
    "read_plus_write": [
        _step("s1", "calendar.read", description="read cal"),
        _step("s2", "email.send", risk="high", description="send email"),
    ],
    "pure_write": [_step("s1", "email.send", risk="high", description="send email")],
    "fast_respond": [_step("s1", "respond", description="answer")],
    "fast_reason": [_step("s1", "reason", description="think")],
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
async def test_single_lead_every_shape_yields_presentation(shape):
    plan = PlanOutput(goal="g", reasoning="r", steps=_SHAPES[shape])
    chat, rec = _make_chat(lead_text="THE_REPLY")
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        stream = await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()

    # Single-lead path taken, per-step agents NOT invoked.
    assert len(rec.lead_calls) == 1
    assert not rec.called_agent("presenter")
    assert not rec.called_agent("perceiver")
    # Exactly one response (the re-homed lead reply); never reply-less.
    assert _responses(stream) == ["THE_REPLY"]
    assert _events(stream)[-1] == "done"


async def test_single_lead_system_set_goal_runs_deterministically():
    """system.* steps run before the lead (Planner-produced, no data dep); the lead still
    replies."""
    plan = PlanOutput(
        goal="remember",
        reasoning="r",
        steps=[
            _step("s1", "system.set_goal", description="set a goal"),
            _step("s2", "respond", description="confirm"),
        ],
    )
    chat, rec = _make_chat(lead_text="Done.")
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        stream = await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()

    handler = chat._system_capability_handler.handle_system_capability
    handler.assert_awaited_once()
    args = handler.await_args.args
    assert args[0].capability == "system.set_goal"
    assert args[1] is plan
    assert args[2] == "usr_1"
    assert args[3] == "ws_1"
    # The single-lead path ran and the lead still replied.
    assert len(rec.lead_calls) == 1
    assert _responses(stream) == ["Done."]


async def test_single_lead_skips_user_actor_system_steps():
    """A user-actor system.* step is NOT executed deterministically (actor guard)."""
    plan = PlanOutput(
        goal="g",
        reasoning="r",
        steps=[_step("s1", "system.set_goal", actor="user", description="user goal")],
    )
    chat, rec = _make_chat()
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()
    chat._system_capability_handler.handle_system_capability.assert_not_awaited()


async def test_single_lead_emits_user_actions_ready():
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    user_steps = [
        _step("u1", "email.reply", actor="user", description="Reply", user_context="urgent")
    ]
    chat, rec = _make_chat()
    ctx = _patches(plan, user_steps)
    for c in ctx:
        c.start()
    try:
        stream = await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()
    ua = [e for e in stream if e.get("event") == "user_actions"]
    assert ua and ua[0]["steps"] == [{"description": "Reply", "context": "urgent"}]


# ── Output re-homing (C-CORR2) ────────────────────────────────────────────────


async def test_single_lead_rehomes_output_stripped_reply_raw_surface_and_learner():
    """The lead's agent_done text is re-homed: Presentation gets the STRIPPED text,
    while the shared tail's surface push + learner receive the RAW text."""
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat(lead_text="REPLY_RAW")

    # Learner records the exact agent_response the shared tail spawns.
    learner = MagicMock()
    learner.learn = MagicMock(return_value=MagicMock())
    chat._interaction_learner = learner

    spec = MagicMock()
    spec.should_surface = True

    ctx = _patches(plan, []) + [
        patch(f"{_LEAD}.strip_surface_blocks", new=lambda t: f"STRIPPED::{t}"),
        patch(f"{_LEAD}.extract_surface_spec", new=MagicMock(return_value=spec)),
    ]
    for c in ctx:
        c.start()
    try:
        stream = await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()

    # Presentation carries the STRIPPED reply (chat-visible).
    assert _responses(stream) == ["STRIPPED::REPLY_RAW"]
    # Shared tail surface push gets the RAW presenter_text.
    push = chat._surfaces.push_presenter_surface
    push.assert_awaited_once()
    assert push.await_args.kwargs["response_text"] == "REPLY_RAW"
    # Learner gets the RAW presenter_text as agent_response.
    learner.learn.assert_called_once()
    assert learner.learn.call_args.kwargs["agent_response"] == "REPLY_RAW"


async def test_single_lead_builds_lead_with_plan_steps_scope_and_raw_message():
    """build_chat_lead receives plan.steps (plan-union scope, not a broad scope); the RAW
    user message is the human turn; plan summary + context go into the system context_block."""
    steps = [_step("s1", "calendar.read"), _step("s2", "email.send", risk="high")]
    plan = PlanOutput(goal="THE_GOAL", reasoning="THE_REASON", steps=steps)
    chat, rec = _make_chat()
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        await _run_stream(chat, message="do the thing", permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()

    chat._invoker.build_chat_lead.assert_awaited_once()
    b_args = chat._invoker.build_chat_lead.await_args.args
    assert b_args[0] == plan.steps
    assert b_args[1] == "ws_1"

    assert len(rec.lead_calls) == 1
    call = rec.lead_calls[0]
    assert call["lead"] is chat._fake_lead
    # RAW human message, NOT the plan summary.
    assert call["message"] == "do the thing"
    # tools omitted → internal resolve (None), NOT a caller-supplied set.
    assert call["tools"] is None
    # Plan goal/reasoning + assembled context live in the system context_block.
    assert "THE_GOAL" in call["context_block"]
    assert "THE_REASON" in call["context_block"]
    assert "LEAD_CTX" in call["context_block"]
    assert "do the thing" not in call["context_block"]


async def test_single_lead_completes_with_run_completed():
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat()
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        stream = await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()
    assert _events(stream)[-1] == "done"


# ── Authority: permission_mode x presence, each downgrade-only ────────────────────


async def test_auto_mode_is_forwarded_verbatim_to_the_lead():
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat()
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        stream = await _run_stream(chat, permission_mode="auto")
    finally:
        for c in ctx:
            c.stop()
    assert len(rec.lead_calls) == 1
    assert rec.lead_calls[0]["permission_mode"] == "auto"  # mode forwarded
    # No per-step agent was invoked — the lead is the only agent after the Planner.
    assert not rec.called_agent("presenter")
    assert not rec.called_agent("perceiver")
    assert _events(stream)[-1] == "done"


@pytest.mark.parametrize("perm", ["ask", "auto"])
async def test_no_durable_checkpointer_downgrades_presence_to_absent(perm):
    """Durable precondition: a pause is only worth taking if it can be RESUMED. With no
    durable checkpointer (MemorySaver/none -> has_durable_checkpointer()==False) the turn
    still runs its lead, but as ``presence="absent"`` — so a gated write is PREPARED for
    review instead of interrupting into a thread nothing can re-enter."""
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat(durable=False)
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        stream = await _run_stream(chat, permission_mode=perm)
    finally:
        for c in ctx:
            c.stop()
    assert len(rec.lead_calls) == 1
    assert rec.lead_calls[0]["presence"] == "absent"
    assert rec.lead_calls[0]["permission_mode"] == perm
    assert _events(stream)[-1] == "done"


async def test_bypass_without_durable_checkpointer_downgrades_to_auto():
    """``bypass`` promises "do not interrupt me", which is only meaningful with a *me* on
    the turn. No durable checkpointer -> presence ``absent`` -> bypass becomes ``auto``."""
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat(durable=False)
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()
    assert len(rec.lead_calls) == 1
    assert rec.lead_calls[0]["permission_mode"] == "auto"


async def test_bypass_non_entitled_workspace_downgrades_to_auto():
    """Entitlement: bypass on a workspace that has NOT opted in (workspace_allows_bypass ->
    False) downgrades to ``auto`` (fail-safe, never silently granting bypass)."""
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat()
    ctx = _patches(plan, [], allow_bypass=False)
    for c in ctx:
        c.start()
    try:
        await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()
    assert len(rec.lead_calls) == 1
    # bypass was downgraded to auto and that mode was forwarded to the lead.
    assert rec.lead_calls[0]["permission_mode"] == "auto"


async def test_unknown_permission_mode_fails_closed_to_ask():
    """An unrecognised mode is not an opt-out — it confirms every write."""
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat()
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        await _run_stream(chat, permission_mode="whatever")
    finally:
        for c in ctx:
            c.stop()
    assert len(rec.lead_calls) == 1
    assert rec.lead_calls[0]["permission_mode"] == "ask"


# ── Security: permission_mode is INDEPENDENT of the legacy ``mode`` slot ──────────


@pytest.mark.parametrize("legacy_mode", ["execute", "ask", "plan"])
async def test_legacy_mode_slot_never_grants_authority(legacy_mode):
    """schedule_dispatch (``mode="execute"``) and routes_ws surface actions (``mode="ask"``)
    both arrive with the DEFAULT permission_mode. The legacy ``mode`` slot must not be
    read as authority: whatever it says, the lead is handed ``auto``."""
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat()
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        await _run_batch(chat, mode=legacy_mode, surface="scheduler")
    finally:
        for c in ctx:
            c.stop()
    assert len(rec.lead_calls) == 1
    assert rec.lead_calls[0]["permission_mode"] == "auto"


@pytest.mark.parametrize("perm", ["bypass", "ask", "auto"])
async def test_batch_entry_is_absent_and_never_reaches_bypass(perm):
    """The batch entry passes ``presence="absent"``: no synchronous user is on the turn to
    answer a confirmation. The turn still runs its lead (that is the collapse — there is no
    other arm), but ``absent`` makes the gate PREPARE a confirmable write rather than
    interrupt into a void, and it downgrades ``bypass`` to ``auto``."""
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    chat, rec = _make_chat(lead_text="BATCH_REPLY")
    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        result = await _run_batch(chat, mode="execute", permission_mode=perm)
    finally:
        for c in ctx:
            c.stop()
    assert len(rec.lead_calls) == 1
    assert rec.lead_calls[0]["presence"] == "absent"
    assert rec.lead_calls[0]["permission_mode"] != "bypass"
    # The batch turn still produces its terminal reply.
    assert result["presentation"] == "BATCH_REPLY"


async def test_single_lead_pause_suspends_turn_and_skips_tail():
    """Pause seam (P2.3): an ask-mode single-lead turn whose stream_deep_lead yields an
    ``approval_needed`` frame emits a typed ApprovalRequired and STOPS — no Presentation,
    no ``done`` (RunCompleted), and the surface/learner completion tail is NOT run — while
    finish_trace still runs (the ``finally``)."""
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "email.send", risk="high")])
    chat, rec = _make_chat()

    # A learner + surface that MUST NOT be touched on a suspended turn.
    learner = MagicMock()
    learner.learn = MagicMock(return_value=MagicMock())
    chat._interaction_learner = learner

    async def _pausing_stream_deep_lead(
        lead, tools=None, *, permission_mode=None, presence=None, **kw
    ):
        rec.lead_calls.append({"permission_mode": permission_mode, "presence": presence})
        yield {"event": "agent_start", "agent": "lead", "model": "m"}
        yield {
            "event": "approval_needed",
            "agent": "lead",
            "approval_id": "apr_1",
            "capability": "email.send",
            "risk_level": "high",
            "thread_id": "c:ws_1:t1",
        }
        # A terminal reply the gate would only produce AFTER resume — must never be reached.
        yield {"event": "agent_done", "agent": "lead", "text": "SHOULD_NOT_APPEAR"}

    chat._invoker.stream_deep_lead = _pausing_stream_deep_lead

    ctx = _patches(plan, [])
    for c in ctx:
        c.start()
    try:
        stream = await _run_stream(chat, permission_mode="ask")
    finally:
        for c in ctx:
            c.stop()

    events = _events(stream)
    # The pause frame is emitted...
    approval_frames = [e for e in stream if e.get("event") == "approval_needed"]
    assert len(approval_frames) == 1
    assert approval_frames[0] == {
        "event": "approval_needed",
        "approval_id": "apr_1",
        "capability": "email.send",
        "risk_level": "high",
        "thread_id": "c:ws_1:t1",
    }
    # ...and the turn STOPS: no reply, no completion frame.
    assert "response" not in events
    assert "done" not in events
    assert "SHOULD_NOT_APPEAR" not in "".join(str(e) for e in stream)
    # The completion tail (surface push + learner spawn) never ran for a suspended turn.
    chat._surfaces.push_presenter_surface.assert_not_awaited()
    learner.learn.assert_not_called()
    # finish_trace STILL ran (the finally survives the early return).
    chat._trace_manager.finish_trace.assert_awaited_once()


# ── Facade plumbing: muldro.py forwards permission_mode (independent field) ──────


async def test_muldro_facade_forwards_permission_mode_events():
    from src.orchestrator.muldro import MuldroOrchestrator

    orch = MuldroOrchestrator.__new__(MuldroOrchestrator)
    orch._chat = MagicMock()
    orch._chat.process_message_events = MagicMock(return_value=iter([]))
    orch.process_message_events(
        message="hi",
        user_id="u",
        workspace_id="w",
        permission_mode="bypass",
    )
    assert orch._chat.process_message_events.call_args.kwargs["permission_mode"] == "bypass"


async def test_muldro_facade_forwards_permission_mode_stream():
    from src.orchestrator.muldro import MuldroOrchestrator

    orch = MuldroOrchestrator.__new__(MuldroOrchestrator)
    orch._chat = MagicMock()
    orch._chat.process_message_stream = MagicMock(return_value=iter([]))
    orch.process_message_stream(
        message="hi",
        user_id="u",
        workspace_id="w",
        permission_mode="bypass",
    )
    assert orch._chat.process_message_stream.call_args.kwargs["permission_mode"] == "bypass"


async def test_muldro_facade_forwards_permission_mode_batch():
    from src.orchestrator.muldro import MuldroOrchestrator

    orch = MuldroOrchestrator.__new__(MuldroOrchestrator)
    orch._chat = MagicMock()
    orch._chat.process_message = AsyncMock(return_value={})
    await orch.process_message(
        message="hi",
        user_id="u",
        workspace_id="w",
        permission_mode="bypass",
    )
    assert orch._chat.process_message.call_args.kwargs["permission_mode"] == "bypass"


async def test_chat_request_permission_mode_default_is_non_bypass():
    from src.api.routes_chat import ChatRequest

    # P3c: the default is now None (omitted) → the interactive handler substitutes the
    # per-workspace default (fail-safe auto). The raw request default is NEVER bypass, preserving
    # the C-SEC3 safety invariant (an omitted permission_mode can never silently activate bypass).
    req = ChatRequest(message="hi")
    assert req.permission_mode is None
    assert req.permission_mode != "bypass"
    # P3b: the legacy ``mode`` slot is removed from the contract; a client-sent ``mode`` is ignored
    # and never influences ``permission_mode`` (independence preserved).
    req2 = ChatRequest.model_validate({"message": "hi", "mode": "execute"})
    assert req2.permission_mode is None
    assert not hasattr(req2, "mode")


# ── P2.5c: planless reroute (MULDRO_CHAT_PLANLESS) ──────────────────────────────────────
#
# When chat_planless is ON, _process_core drops the Planner entirely and routes the turn
# through ONE connector-scoped lead. Flag-OFF must be byte-identical (the existing tests
# above, all with chat_planless=False via make_mock_settings, already pin that). These pin
# the flag-ON behavior.


def _planless_patches(*, allow_bypass: bool = True):
    """Patch the plan-machinery seams as ``assert-not-called`` sentinels — the planless path
    must touch NONE of them — plus the bypass entitlement helper (True by default)."""
    return {
        "classify_intent": patch(
            f"{_MOD}.classify_intent", new=AsyncMock(return_value=("x", 0.9, []))
        ),
        "extract_plan": patch(f"{_MOD}.extract_plan", new=MagicMock()),
        "intent_to_plan": patch(f"{_MOD}.intent_to_plan", new=MagicMock()),
        "resolve_plan_routing": patch(
            f"{_MOD}.resolve_plan_routing", new=AsyncMock(return_value=([], []))
        ),
        "workspace_allows_bypass": patch(
            f"{_MOD}.workspace_allows_bypass", new=AsyncMock(return_value=allow_bypass)
        ),
    }


def _make_planless_chat(**overrides):
    chat, rec = _make_chat(settings_overrides={"chat_planless": True, **overrides})
    # _make_chat only wires build_chat_lead; the planless path uses build_planless_lead.
    chat._invoker.build_planless_lead = AsyncMock(return_value=chat._fake_lead)
    return chat, rec


async def test_planless_drops_the_planner_entirely():
    """Flag ON → NONE of the plan machinery runs, no PlanReady, the lead is built from
    connectors (build_planless_lead), and the turn still replies + completes."""
    chat, rec = _make_planless_chat()
    p = _planless_patches()
    mocks = {k: v.start() for k, v in p.items()}
    try:
        stream = await _run_stream(chat, permission_mode="bypass")
    finally:
        for v in p.values():
            v.stop()

    # The Planner + fast-path + routing seams were NEVER touched.
    mocks["classify_intent"].assert_not_called()
    mocks["extract_plan"].assert_not_called()
    mocks["intent_to_plan"].assert_not_called()
    mocks["resolve_plan_routing"].assert_not_called()
    # The planned build_chat_lead was NOT used; the planless one WAS.
    chat._invoker.build_planless_lead.assert_awaited_once_with("usr_1", "ws_1")
    chat._invoker.build_chat_lead.assert_not_awaited()
    # No PlanReady in the SSE stream; exactly the re-homed lead reply; terminal done.
    assert "plan" not in _events(stream)
    assert _responses(stream) == ["LEAD_REPLY"]
    assert _events(stream)[-1] == "done"


async def test_planless_logs_interaction_without_a_plan():
    """The planless turn still logs the interaction (audit) — with plan=None, intent=None."""
    chat, rec = _make_planless_chat()
    p = _planless_patches()
    for v in p.values():
        v.start()
    try:
        stream = await _run_stream(chat, permission_mode="bypass")
    finally:
        for v in p.values():
            v.stop()

    chat._plans.log_interaction.assert_awaited_once()
    kwargs = chat._plans.log_interaction.await_args.kwargs
    assert kwargs["plan"] is None
    assert kwargs["intent"] is None
    # The turn still reached its terminal reply (InteractionLogged is batch-only, not in SSE).
    assert _events(stream)[-1] == "done"


async def test_planless_no_system_pre_run_no_user_actions():
    """Planless drops the deterministic system.* pre-run (the lead calls its own tools) AND
    UserActionsReady (no plan → no user steps)."""
    chat, rec = _make_planless_chat()
    p = _planless_patches()
    for v in p.values():
        v.start()
    try:
        stream = await _run_stream(chat, permission_mode="bypass")
    finally:
        for v in p.values():
            v.stop()

    chat._system_capability_handler.handle_system_capability.assert_not_awaited()
    assert "user_actions" not in _events(stream)


async def test_planless_flag_off_still_runs_the_planner():
    """Flag OFF (chat_planless=False) → the Planner path runs exactly as today
    (byte-identical): classify_intent IS called and the planless lead is never built."""
    chat, rec = _make_chat()
    chat._invoker.build_planless_lead = AsyncMock(return_value=chat._fake_lead)
    plan = PlanOutput(goal="g", reasoning="r", steps=[_step("s1", "respond")])
    ctx = _patches(plan, [])
    started = [c.start() for c in ctx]
    try:
        await _run_stream(chat, permission_mode="bypass")
    finally:
        for c in ctx:
            c.stop()

    started[0].assert_awaited()  # classify_intent WAS called on the flag-off path
    chat._invoker.build_planless_lead.assert_not_awaited()  # planless never entered


async def test_planless_entered_in_auto_mode_not_bypass_only():
    """The planless reroute is not bypass-only: an `auto`-mode turn (durable checkpointer
    present) is routed planless too, with permission_mode threaded into the shared stream so
    ask/auto action-time gating still applies."""
    chat, rec = _make_planless_chat()  # durable=True by default → auto not downgraded
    p = _planless_patches()
    for v in p.values():
        v.start()
    try:
        stream = await _run_stream(chat, permission_mode="auto")
    finally:
        for v in p.values():
            v.stop()

    chat._invoker.build_planless_lead.assert_awaited_once_with("usr_1", "ws_1")
    # permission_mode="auto" was threaded through to the deep stream (gate stays live).
    assert rec.lead_calls and rec.lead_calls[0]["permission_mode"] == "auto"
    assert _responses(stream) == ["LEAD_REPLY"]
    assert _events(stream)[-1] == "done"
