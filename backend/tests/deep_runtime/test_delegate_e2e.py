"""Step 7B2 P6: LOAD-BEARING forced-on offline e2e for the deep-delegate layer.

Drives the REAL delegate machinery end-to-end, fully OFFLINE (no Anthropic API, no
Postgres, no Redis):

    AgentInvoker._build_deep_agent_for   (prepends the Governor delegate-critique when
                                          deep_delegates_enabled, installs the full gated
                                          middleware chain on the lead)
      -> build_deep_agent / create_deep_agent(subagents=[<perceiver delegate>])
      -> stream_deep_agent_events         (the frozen SSE adapter)

A scripted-fake LEAD model emits ONE ``task`` call routed to a read-only Perceiver delegate,
then a terminal text turn. A scripted-fake CHILD (the delegate) emits an IN-scope read
(``internal_search`` -> ``internal.search``, in the Perceiver's scope) AND an OUT-of-scope
write (``email_send`` -> ``email.send``, NOT in scope) in turn 1, then a text summary in
turn 2. A fake Anthropic critique client returns ``{"ok": true, "concerns": []}``.

The delegate is built directly via ``build_read_only_delegate`` and passed as ``subagents=``
(the ``deep_delegates_enabled`` flag-BRANCH in ``call_agent_stream`` is separately covered by
``test_agent_invoker_delegates.py``); here we exercise the build + stream seam itself.

Guard (positive) assertions:
  1. the child's IN-scope read reaches the recording ``execute_tool``;
  2. the child's OUT-of-scope write NEVER reaches ``execute_tool`` (denied by the child's OWN
     capability_scope guard);
  3. the ``task`` tool_result carries the child summary AND the critique's ``unreviewed``
     annotation, and is not ``blocked`` (a read is never blocked);
  4. no ``error`` frame (the tool shells never ran / nothing raised);
  5. the ambient general-purpose ``task`` child is GONE while the delegate still routes
     (``task(__list__)`` probe);
  6. the frozen SSE frame set is intact (agent_start ... agent_done).

Each guard has a paired NEGATIVE CONTROL that asserts the FAILURE MODE so a regression that
removes the guard makes the positive test AND the control diverge:
  (a) child built WITHOUT its capability_scope (db_factory=None, empty scope) -> the SAME
      out-of-scope write EXECUTES;
  (b) lead built with deep_delegates_enabled=False -> the ``task`` tool_result has NO
      ``unreviewed`` annotation;
  (c) flag OFF + subagents=() -> the lead is delegate-free (GP present, no delegate).

THE ONE CRITICAL HAZARD: ``disable_general_purpose_subagent`` mutates the PROCESS-GLOBAL
deepagents ``_HARNESS_PROFILES`` registry. The ``_restore_harness_profiles`` autouse fixture
(copied verbatim from ``test_delegate_builder.py``) snapshots + RESTORES the keys these tests
touch — restoring the built-in ``anthropic:claude-sonnet-4-6`` profile rather than popping it —
so a GP-disable never leaks into the wider sonnet-lead deep suite.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from src.deep_runtime import agent_builder
from src.deep_runtime.delegates import (
    build_read_only_delegate,
    disable_general_purpose_subagent,
)
from src.deep_runtime.prompt_bridge import build_system_message
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent, ThinkingConfig, create_sub_agents
from tests.conftest import make_mock_settings

WS = "ws_delegate_e2e"
USER = "user_delegate_e2e"
MODEL_ID = "claude-sonnet-4-6"

CHILD_ANSWER = "CHILD-REPLY: found 42 results in the knowledge base."

# internal_search -> internal.search (IN Perceiver scope); email_send -> email.send
# (a write cap, NOT in Perceiver scope).
_NAME_TO_CAP: dict[str, str | None] = {
    "internal_search": "internal.search",
    "email_send": "email.send",
}
_TOOL_DEFS = [
    {"name": "internal_search", "description": "search knowledge"},
    {"name": "email_send", "description": "send email"},
]

CAP_SCOPE_TOOL_REGISTRY = "src.deep_runtime.middleware.capability_scope.ToolRegistry"


# ---------------------------------------------------------------------------
# Offline capability resolution: stub ToolRegistry with a name->capability map.
# ---------------------------------------------------------------------------
class _FakeToolDef:
    def __init__(self, capability: str | None) -> None:
        self.capability = capability


class _FakeRegistry:
    def __init__(self, db: Any, workspace_id: str | None = None) -> None:  # noqa: ARG002
        pass

    async def get_tool(self, name: str) -> _FakeToolDef | None:
        cap = _NAME_TO_CAP.get(name)
        return _FakeToolDef(cap) if cap is not None else None


def _fake_db_factory():
    """An async-context-manager factory yielding a sentinel DB (matches test_delegate_builder)."""

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _recorder(rec: list[tuple[str, dict]]):
    """A fake ``execute_tool(name, args, user_id, workspace_id)`` recording each call."""

    async def _execute(name: str, args: dict, user_id: str, workspace_id: str) -> dict:  # noqa: ARG001
        rec.append((name, args))
        return {"results": ["r1", "r2"], "count": 42}

    return _execute


def _fake_critique_client(*, ok: bool = True, concerns: list[str] | None = None) -> MagicMock:
    """A fake Anthropic client whose ``messages.create`` returns a scripted JSON verdict.

    Mirrors ``test_governor_delegate_critique._fake_client`` — the critique middleware reads
    ``response.content[0].text`` and parses it as the ``{"ok", "concerns"}`` verdict.
    """
    client = MagicMock()
    payload = json.dumps({"ok": ok, "concerns": concerns or []})
    resp = SimpleNamespace(content=[SimpleNamespace(text=payload)])
    client.messages.create = AsyncMock(return_value=resp)
    return client


def _empty_scope_read_only() -> SubAgent:
    """A read-only Perceiver config with EMPTY capability_scope (deny-all if guarded; no
    write caps -> no ValueError at construction even without a db_factory)."""
    return SubAgent(
        name="perceiver",
        prompt=create_sub_agents()["perceiver"].prompt,
        model_tier="sonnet",
        capability_scope=set(),
        max_tokens=4096,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=False, budget_tokens=0),
    )


def _lead() -> SubAgent:
    """The deep LEAD (a distinct name from ``perceiver`` so the build_chat_model dispatch
    routes it to the lead fake). Empty scope -> the lead build never trips the write-cap
    precheck; the lead only ever calls the builtin ``task`` tool."""
    return SubAgent(
        name="presenter",
        prompt="You are the lead.",
        model_tier="sonnet",
        capability_scope=set(),
    )


# ---------------------------------------------------------------------------
# Scripted streaming fake chat model — turn chosen by inbound ToolMessage count.
# Carries ``model_name`` + forces provider="anthropic" so deepagents' harness-profile
# key resolves to ``anthropic:<model_name>`` exactly as a real ChatAnthropic would
# (needed for the GP-disable probe).  Copied from spikes/deep_delegate/subagent_gated_probe.py.
# ---------------------------------------------------------------------------
class ScriptedModel(BaseChatModel):
    model_name: str = MODEL_ID

    _turns: list[list[AIMessageChunk]]

    def __init__(self, turns: list[list[AIMessageChunk]], model_name: str = MODEL_ID) -> None:
        super().__init__(model_name=model_name)
        object.__setattr__(self, "_turns", turns)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake-e2e"

    def _get_ls_params(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return {"ls_provider": "anthropic", "ls_model_name": self.model_name}

    def bind_tools(self, tools: Any, **kwargs: Any):  # noqa: ANN003, ARG002
        return self

    def _select_turn(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        tool_rounds = sum(1 for m in messages if isinstance(m, ToolMessage))
        idx = min(tool_rounds, len(self._turns) - 1)
        return self._turns[idx]

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._select_turn(messages):
            gen = ChatGenerationChunk(message=chunk)
            if run_manager is not None:
                await run_manager.on_llm_new_token(_chunk_text(chunk), chunk=gen)
            yield gen

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        merged: AIMessageChunk | None = None
        async for gen in self._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            merged = gen.message if merged is None else merged + gen.message
        assert merged is not None
        msg = AIMessage(
            content=merged.content,
            tool_calls=list(merged.tool_calls),
            usage_metadata=merged.usage_metadata,
            response_metadata=merged.response_metadata,
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError("sync generate not used in this async e2e")


def _chunk_text(chunk: AIMessageChunk) -> str:
    if isinstance(chunk.content, str):
        return chunk.content
    parts: list[str] = []
    for block in chunk.content:
        if isinstance(block, dict):
            parts.append(block.get("text") or block.get("thinking") or "")
    return "".join(parts)


def _usage_chunk(stop_reason: str) -> AIMessageChunk:
    return AIMessageChunk(
        content=[],
        usage_metadata={
            "input_tokens": 50,
            "output_tokens": 10,
            "total_tokens": 60,
            "input_token_details": {"cache_read": 0, "cache_creation": 0},
        },
        response_metadata={"model_name": MODEL_ID, "stop_reason": stop_reason},
    )


def _lead_task_turns() -> list[list[AIMessageChunk]]:
    """Lead turn 0 delegates via task(perceiver); turn 1 answers with terminal text."""
    return [
        [
            AIMessageChunk(content=[{"type": "text", "text": "Delegating.", "index": 0}]),
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="task",
                        args=json.dumps({"subagent_type": "perceiver", "description": "look up X"}),
                        id="lead_tc1",
                        index=1,
                    )
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": "Lead final answer.", "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


def _lead_list_turns() -> list[list[AIMessageChunk]]:
    """Lead turn 0 calls task(subagent_type='__list__') — an unknown type, so the task tool
    errors naming every ALLOWED subagent type; turn 1 answers."""
    return [
        [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="task",
                        args=json.dumps({"subagent_type": "__list__", "description": "x"}),
                        id="lead_list_tc",
                        index=0,
                    )
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": "done", "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


def _child_turns() -> list[list[AIMessageChunk]]:
    """Child turn 0 calls an IN-scope read AND an OUT-of-scope write; turn 1 summarizes."""
    return [
        [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="internal_search",
                        args=json.dumps({"query": "X"}),
                        id="child_read",
                        index=0,
                    ),
                    tool_call_chunk(
                        name="email_send",
                        args=json.dumps({"to": "a@b.com", "body": "hi"}),
                        id="child_write",
                        index=1,
                    ),
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": CHILD_ANSWER, "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


def _build_chat_model_dispatch(lead_fake: ScriptedModel, child_fake: ScriptedModel):
    """Return a build_chat_model replacement dispatching by agent name: the perceiver
    delegate gets the child fake, every other agent (the lead) gets the lead fake."""

    def _dispatch(agent: SubAgent) -> ScriptedModel:
        return child_fake if agent.name == "perceiver" else lead_fake

    return _dispatch


def _make_invoker(*, deep_delegates_enabled: bool, client: MagicMock) -> AgentInvoker:
    """A real AgentInvoker (runtime=deep) wired for the OFFLINE drive.

    ``services=None`` -> every ``services.extras.get("redis")`` guard resolves to None (no
    Redis). ``client`` is the fake critique client. The lead's own ``execute_tool`` is never
    reached (the lead only calls the builtin ``task``), but ToolExecutor must exist.
    ``db_factory_provider`` yields the fake factory the (never-entered, task-only) lead
    capability_scope guard would use.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])
    tool_executor.execute_tool = _recorder([])  # lead never dispatches a Jarvis tool

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    lead = _lead()
    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", deep_delegates_enabled=deep_delegates_enabled),
        client=client,
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _fake_db_factory(),
        tool_executor=tool_executor,
        context=context,
        agents={lead.name: lead},
        checkpointer_provider=lambda: None,
    )


async def _build_lead_and_stream(
    invoker: AgentInvoker,
    lead: SubAgent,
    *,
    subagents: list | tuple,
    thread_id: str,
) -> list[dict]:
    """Build the deep lead via the REAL ``_build_deep_agent_for`` and collect adapter frames."""
    deep_agent = await invoker._build_deep_agent_for(
        lead,
        [],  # the lead carries no Jarvis tools — it only routes via the builtin task tool
        user_id=USER,
        workspace_id=WS,
        thread_id=thread_id,
        authorization_source="direct_user_request",
        system_prompt=build_system_message(invoker.build_system_prompt(lead, "")),
        subagents=subagents,
    )
    return [
        frame
        async for frame in stream_deep_agent_events(
            deep_agent,
            {"messages": [{"role": "user", "content": "research X"}]},
            {"configurable": {"thread_id": thread_id}},
            agent_name="presenter",
            model=MODEL_ID,
        )
    ]


def _task_result_frame(frames: list[dict]) -> dict | None:
    return next((f for f in frames if f["event"] == "tool_result" and f["tool"] == "task"), None)


def _allowed_subagent_types(frames: list[dict]) -> set[str]:
    """Read the ALLOWED subagent-type set off the task(__list__) error tool_result.

    The task tool returns an error naming '...the only allowed types are `perceiver`,
    `general-purpose`'. A regex over backtick-quoted names survives the critique's
    JSON-annotation wrapping (json.dumps does not escape backticks)."""
    tr = _task_result_frame(frames)
    if tr is None:
        return set()
    return set(re.findall(r"`([^`]+)`", str(tr.get("result", ""))))


# ═══════════════════════════════════════════════════════════════════════════════
# THE ONE CRITICAL HAZARD: process-global _HARNESS_PROFILES restore (copied verbatim
# from test_delegate_builder.py). RESTORE the built-in profile, do NOT naive-pop.
# ═══════════════════════════════════════════════════════════════════════════════
_GP_TEST_KEYS = ("anthropic:claude-sonnet-4-6", "anthropic:claude-opus-4-8")


@pytest.fixture(autouse=True)
def _restore_harness_profiles():
    """Snapshot + restore the process-global harness-profile registry for the keys these
    tests register under, so a GP-disable never leaks into the wider suite. Ensures the
    built-in profile bootstrap has run BEFORE snapshotting so teardown restores the real
    built-in byte-for-byte instead of destroying it."""
    from deepagents.profiles.harness.harness_profiles import (
        _HARNESS_PROFILES,
        _ensure_harness_profiles_loaded,
    )

    _ensure_harness_profiles_loaded()
    saved = {k: _HARNESS_PROFILES.get(k) for k in _GP_TEST_KEYS}
    try:
        yield
    finally:
        for key, prev in saved.items():
            if prev is None:
                _HARNESS_PROFILES.pop(key, None)
            else:
                _HARNESS_PROFILES[key] = prev


# ═══════════════════════════════════════════════════════════════════════════════
# POSITIVE GUARD — assertions 1,2,3,4,6.
# ═══════════════════════════════════════════════════════════════════════════════
async def test_forced_on_child_gate_and_critique_annotation():
    rec: list = []
    lead_fake = ScriptedModel(_lead_task_turns())
    child_fake = ScriptedModel(_child_turns())
    invoker = _make_invoker(deep_delegates_enabled=True, client=_fake_critique_client(ok=True))
    lead = invoker._agents["presenter"]

    disable_general_purpose_subagent(MODEL_ID)

    with (
        patch.object(
            agent_builder, "build_chat_model", _build_chat_model_dispatch(lead_fake, child_fake)
        ),
        patch(CAP_SCOPE_TOOL_REGISTRY, _FakeRegistry),
    ):
        delegate = await build_read_only_delegate(
            create_sub_agents()["perceiver"],
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec),
        )
        frames = await _build_lead_and_stream(
            invoker, lead, subagents=[delegate], thread_id="e2e-positive"
        )

    # (1) the child's IN-scope read reached the recording execute_tool.
    assert ("internal_search", {"query": "X"}) in rec, f"in-scope read not dispatched; rec={rec}"

    # (2) the child's OUT-of-scope write NEVER reached execute_tool (denied by the child's own
    #     capability_scope guard — the delegate is read-only).
    assert not any(n == "email_send" for n, _ in rec), (
        f"out-of-scope write executed — child gate did not fire; rec={rec}"
    )

    # (3) the task tool_result carries the child summary AND the critique's unreviewed
    #     annotation, and is not blocked (a read is never blocked).
    task_result = _task_result_frame(frames)
    assert task_result is not None, f"no task tool_result; events={[f['event'] for f in frames]}"
    content = str(task_result["result"])
    assert "unreviewed" in content, f"critique annotation missing; content={content!r}"
    assert CHILD_ANSWER in content, f"child summary missing from task result; content={content!r}"
    assert not task_result.get("blocked"), "a read delegate summary must never be blocked"

    # (4) no error frame — the inert tool shells never ran and nothing raised.
    assert not any(f["event"] == "error" for f in frames), (
        f"unexpected error frame(s): {[f for f in frames if f['event'] == 'error']}"
    )

    # (6) the frozen SSE frame set is intact.
    events = [f["event"] for f in frames]
    assert events[0] == "agent_start"
    assert "agent_done" in events
    assert "tool_call" in events  # the lead's task call
    assert "tool_result" in events


# ═══════════════════════════════════════════════════════════════════════════════
# POSITIVE GUARD — assertion 5: GP disabled while the delegate still routes.
# ═══════════════════════════════════════════════════════════════════════════════
async def test_forced_on_gp_disabled_and_delegate_routes():
    rec: list = []
    lead_fake = ScriptedModel(_lead_list_turns())
    child_fake = ScriptedModel(_child_turns())
    invoker = _make_invoker(deep_delegates_enabled=True, client=_fake_critique_client(ok=True))
    lead = invoker._agents["presenter"]

    disable_general_purpose_subagent(MODEL_ID)

    with (
        patch.object(
            agent_builder, "build_chat_model", _build_chat_model_dispatch(lead_fake, child_fake)
        ),
        patch(CAP_SCOPE_TOOL_REGISTRY, _FakeRegistry),
    ):
        delegate = await build_read_only_delegate(
            create_sub_agents()["perceiver"],
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec),
        )
        frames = await _build_lead_and_stream(
            invoker, lead, subagents=[delegate], thread_id="e2e-gp"
        )

    allowed = _allowed_subagent_types(frames)
    assert allowed, f"could not read allowed subagent types; frames={frames}"
    # (5) the ambient general-purpose task child is GONE, and our delegate still routes.
    assert "general-purpose" not in allowed, f"GP still present after disable; allowed={allowed}"
    assert "perceiver" in allowed, f"delegate vanished; allowed={allowed}"


# ═══════════════════════════════════════════════════════════════════════════════
# NEGATIVE CONTROL (a) — teeth for assertion 2: a child built WITHOUT its capability_scope
# guard EXECUTES the same out-of-scope write. Toggle: db_factory=None + empty scope (the
# same construction test_delegate_builder uses for its guard-vs-no-guard control).
# ═══════════════════════════════════════════════════════════════════════════════
async def test_neg_control_no_child_scope_out_of_scope_executes():
    rec: list = []
    lead_fake = ScriptedModel(_lead_task_turns())
    child_fake = ScriptedModel(_child_turns())
    invoker = _make_invoker(deep_delegates_enabled=True, client=_fake_critique_client(ok=True))
    lead = invoker._agents["presenter"]

    disable_general_purpose_subagent(MODEL_ID)

    with (
        patch.object(
            agent_builder, "build_chat_model", _build_chat_model_dispatch(lead_fake, child_fake)
        ),
        patch(CAP_SCOPE_TOOL_REGISTRY, _FakeRegistry),
    ):
        # db_factory=None + empty scope -> NO capability_scope guard installed on the child
        # (and no ValueError, since an empty scope has no write cap to protect).
        delegate = await build_read_only_delegate(
            _empty_scope_read_only(),
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=None,
            execute_tool=_recorder(rec),
        )
        await _build_lead_and_stream(invoker, lead, subagents=[delegate], thread_id="e2e-neg-a")

    # With NO child guard, the SAME out-of-scope write now EXECUTES — proving the child's
    # capability_scope guard (present in the positive test) is what denies it there.
    assert any(n == "email_send" for n, _ in rec), (
        f"negative control did not flip — the out-of-scope write should execute "
        f"without the child guard; rec={rec}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# NEGATIVE CONTROL (b) — teeth for assertion 3: with deep_delegates_enabled=False the
# critique is NOT prepended, so the task tool_result carries NO ``unreviewed`` annotation.
# ═══════════════════════════════════════════════════════════════════════════════
async def test_neg_control_no_critique_no_annotation():
    rec: list = []
    lead_fake = ScriptedModel(_lead_task_turns())
    child_fake = ScriptedModel(_child_turns())
    # FLAG OFF -> _build_deep_agent_for does NOT prepend the delegate critique.
    invoker = _make_invoker(deep_delegates_enabled=False, client=_fake_critique_client(ok=True))
    lead = invoker._agents["presenter"]

    disable_general_purpose_subagent(MODEL_ID)

    with (
        patch.object(
            agent_builder, "build_chat_model", _build_chat_model_dispatch(lead_fake, child_fake)
        ),
        patch(CAP_SCOPE_TOOL_REGISTRY, _FakeRegistry),
    ):
        delegate = await build_read_only_delegate(
            create_sub_agents()["perceiver"],
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec),
        )
        frames = await _build_lead_and_stream(
            invoker, lead, subagents=[delegate], thread_id="e2e-neg-b"
        )

    task_result = _task_result_frame(frames)
    assert task_result is not None, f"no task tool_result; events={[f['event'] for f in frames]}"
    content = str(task_result["result"])
    # The child gate still fires (the delegate is real) — only the critique is absent.
    assert ("internal_search", {"query": "X"}) in rec
    assert not any(n == "email_send" for n, _ in rec)
    # With the flag OFF there is NO critique, so no ``unreviewed`` annotation — while the raw
    # child summary is still delivered. This is the teeth: the positive test asserts the
    # annotation IS present; flipping the flag off makes it ABSENT on the same drive.
    assert "unreviewed" not in content, (
        f"critique annotation present with the flag OFF; content={content!r}"
    )
    assert CHILD_ANSWER in content, f"raw child summary missing; content={content!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# NEGATIVE CONTROL (c) — dormancy/byte-identity leg: flag OFF + subagents=() builds a
# DELEGATE-FREE lead (GP present, no delegate, GP-disable never called by this path).
# ═══════════════════════════════════════════════════════════════════════════════
async def test_neg_control_flag_off_subagents_empty_is_delegate_free():
    lead_fake = ScriptedModel(_lead_list_turns())
    invoker = _make_invoker(deep_delegates_enabled=False, client=_fake_critique_client(ok=True))
    lead = invoker._agents["presenter"]

    # DELIBERATELY do NOT call disable_general_purpose_subagent — the flag-off / no-delegate
    # path never would; the autouse fixture guarantees GP starts enabled for this key.
    with patch.object(agent_builder, "build_chat_model", lambda a: lead_fake):
        frames = await _build_lead_and_stream(invoker, lead, subagents=(), thread_id="e2e-neg-c")

    allowed = _allowed_subagent_types(frames)
    assert allowed, f"could not read allowed subagent types; frames={frames}"
    # The dormant path: the ambient general-purpose child is PRESENT (never disabled) and NO
    # Jarvis delegate is registered — byte-identical to the pre-7B2 delegate-free lead.
    assert "general-purpose" in allowed, f"GP unexpectedly absent (flag-off); allowed={allowed}"
    assert "perceiver" not in allowed, f"a delegate leaked into a flag-off build; allowed={allowed}"
