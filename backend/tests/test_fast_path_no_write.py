"""Fail-closed fence: no mutating fast intent may execute on the ungated inline path (Step 6C).

The chat fast path synthesizes a lightweight plan via ``intent_to_plan`` and executes it
INLINE — skipping the Planner AND GraphExecutor's trust gate + write lock. Today no fast intent
emits a write. These tests are a regression fence:

* ``test_no_fast_intent_emits_a_write_capability`` — every real fast intent stays non-write.
* ``test_predicate_classifies_writes_and_unknowns_as_write_fail_closed`` — the classifier has
  teeth: cataloged writes AND unknown capabilities are writes; safe fast caps are not.
* ``test_write_emitting_fast_intent_diverts_to_planner`` — proves the wiring: a (hypothetical)
  write-emitting fast intent is re-routed to the gated Planner path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.contracts import PlanOutput, PlanStep
from src.orchestrator.chat_processor import _fast_step_is_write
from src.orchestrator.intent_classifier import FAST_INTENTS, intent_to_plan
from tests.conftest import make_mock_settings

_MOD = "src.orchestrator.chat_processor"


@pytest.mark.parametrize("intent", sorted(FAST_INTENTS))
def test_no_fast_intent_emits_a_write_capability(intent):
    """Regression fence: every real fast intent yields a non-write plan today."""
    plan = intent_to_plan(intent, "do the thing", [])
    for step in plan.steps:
        assert not _fast_step_is_write(step.capability), (
            f"fast intent {intent} emitted write-classified capability {step.capability} — "
            "it would execute UNGATED on the inline fast path; route through Planner+gate instead"
        )


def test_predicate_classifies_writes_and_unknowns_as_write_fail_closed():
    """Teeth: cataloged writes AND unknown capabilities classify as write; safe fast caps don't."""
    assert _fast_step_is_write("email.send") is True  # cataloged write
    assert _fast_step_is_write("calendar.create") is True  # cataloged write
    assert _fast_step_is_write("totally.unknown.cap") is True  # unknown -> fail closed
    assert _fast_step_is_write("respond") is False
    assert _fast_step_is_write("reason") is False
    assert _fast_step_is_write("knowledge.search") is False


def test_memory_persistence_is_exempt_but_outbound_writes_are_not():
    """`knowledge.remember` is exempt; nothing outbound gains the same pass.

    The fence stops the INTENT CLASSIFIER — a Haiku call over ten fixed intents — from
    being what authorizes a write. `knowledge.remember` authorizes only
    `internal.store_memory` / `internal.store_preference`: internal, workspace-scoped,
    reversible and asked for in the user's own words. That is the memory analogue of
    `SYSTEM_ACTION_CAPABILITIES`, which the chat write gates already exempt for the same
    reason. Every call still crosses the full middleware chain.

    Teeth: the exemption must not have widened to anything that leaves the workspace.
    """
    from src.orchestrator.chat_processor import _FAST_SAFE_CAPABILITIES

    assert _fast_step_is_write("knowledge.remember") is False
    for outbound in ("email.send", "calendar.create", "messaging.send", "repo.create_pr"):
        assert outbound not in _FAST_SAFE_CAPABILITIES
        assert _fast_step_is_write(outbound) is True


def test_knowledge_remember_grants_no_outbound_capability():
    """Teeth on the scope itself, not just the name: whatever `knowledge.remember` expands
    to must stay inside the workspace."""
    from src.orchestrator.lead_builder import KNOWLEDGE_REMEMBER_CAPABILITIES

    assert all(c.startswith("internal.") for c in KNOWLEDGE_REMEMBER_CAPABILITIES)
    # cataloged read-only stays a non-write:
    from src.integrations.capabilities import CAPABILITY_CATALOG, is_read_only_capability

    read_caps = [c for c in CAPABILITY_CATALOG if is_read_only_capability(c)]
    if read_caps:
        assert _fast_step_is_write(read_caps[0]) is False


# ── Divert-wiring proof ───────────────────────────────────────────────────────


def _make_chat() -> object:
    """Build a ChatProcessor with every collaborator mocked (golden-test pattern).

    ``call_agent_stream`` records the ``(agent_name, message)`` pairs so the test can assert
    which agent path ran; it yields a minimal ``agent_done`` frame for every agent. The lead
    (``stream_deep_lead``) records under the name ``"lead"``.
    """
    from src.orchestrator.chat_processor import ChatProcessor

    chat = ChatProcessor.__new__(ChatProcessor)

    chat._settings = make_mock_settings()

    trace = MagicMock()
    trace.trace_id = "trace_fence"
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
    chat._context.assemble_context = AsyncMock(return_value="")

    chat._perception = MagicMock()
    chat._perception._bump_perception_for_sources = AsyncMock()

    chat._events = MagicMock()
    chat._events.emit_runtime_event = AsyncMock()

    chat._get_available_capabilities = AsyncMock(return_value=[])

    chat._plans = MagicMock()
    chat._plans.persist_plan_record = AsyncMock(side_effect=lambda plan, *a, **k: plan)
    chat._plans.log_interaction = AsyncMock(return_value="ilog_fence")

    chat._system_capability_handler = MagicMock()
    chat._system_capability_handler.handle_system_capability = AsyncMock(return_value="SYS_OK")

    chat._surfaces = MagicMock()
    chat._surfaces.push_presenter_surface = AsyncMock(return_value=None)

    recorded: list[tuple[str, str]] = []

    async def _call_agent_stream(agent_name, *, message, **kw):
        recorded.append((agent_name, message))
        yield {"event": "agent_start", "agent": agent_name, "model": "m"}
        yield {"event": "agent_done", "agent": agent_name, "text": "ok"}

    async def _stream_deep_lead(lead, tools=None, **kw):
        recorded.append(("lead", kw.get("message", "")))
        yield {"event": "agent_start", "agent": "lead", "model": "m"}
        yield {"event": "agent_done", "agent": "lead", "text": "ok"}

    chat._invoker = MagicMock()
    chat._invoker.call_agent_stream = _call_agent_stream
    chat._invoker.build_chat_lead = AsyncMock(return_value=MagicMock(name="lead"))
    chat._invoker.stream_deep_lead = _stream_deep_lead
    chat._invoker.has_durable_checkpointer = MagicMock(return_value=True)
    chat._recorded = recorded  # convenience handle for assertions
    return chat


async def _drive(chat) -> list[dict]:
    return [
        evt
        async for evt in chat.process_message_stream(
            message="check my gmail",
            user_id="usr_1",
            workspace_id="ws_1",
        )
    ]


async def test_write_emitting_fast_intent_diverts_to_planner():
    """A FAST intent whose synthesized plan emits a WRITE must divert to the gated Planner path.

    Without the fence, ``use_planner`` would stay False and the write step would execute inline,
    ungated. The fence flips it to True — proven here by the Planner agent being invoked and the
    ``route_selected`` event carrying ``use_planner=True``.
    """
    chat = _make_chat()

    # Fast intent + high confidence => the fast branch is entered.
    fast = "data_fetch"
    # The (hypothetical) fast plan that emits a write — this is what the fence must catch.
    write_plan = PlanOutput(
        goal="check my gmail",
        steps=[PlanStep(step_id="s1", description="send", capability="email.send", risk="high")],
    )
    # After diverting, the Planner path parses a benign respond plan (no execution needed).
    planner_plan = PlanOutput(
        goal="check my gmail",
        steps=[PlanStep(step_id="s1", description="respond", capability="respond")],
    )

    with (
        patch(f"{_MOD}.classify_intent", new=AsyncMock(return_value=(fast, 0.95, []))),
        patch(f"{_MOD}.intent_to_plan", new=MagicMock(return_value=write_plan)),
        patch(f"{_MOD}.extract_plan", new=MagicMock(return_value=planner_plan)),
        patch(f"{_MOD}.resolve_plan_routing", new=MagicMock(return_value=[])),
    ):
        await _drive(chat)

    agents_called = [name for name, _ in chat._recorded]
    # THE proof: the write-emitting fast intent was re-routed to the Planner (gate + lock),
    # not executed inline. Without the fence, "planner" would never appear.
    assert "planner" in agents_called, (
        "write-emitting fast intent did NOT divert to the Planner path — it would have "
        "executed UNGATED on the inline fast path"
    )
    # Corroborating wiring proof: the route_selected runtime event carries the diverted
    # decision (use_planner=True). emit_runtime_event is fired in the background, so read the
    # synchronously-recorded call args rather than the (never-awaited) coroutine body.
    route_payloads = [
        c.kwargs.get("payload", {})
        for c in chat._events.emit_runtime_event.call_args_list
        if c.args and c.args[0] == "route_selected"
    ]
    assert route_payloads and route_payloads[-1]["use_planner"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
