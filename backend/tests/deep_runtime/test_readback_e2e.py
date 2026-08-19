"""Step 7C P4: LOAD-BEARING forced-on offline e2e for the deep inline read-back verifier.

Phases 1-3 landed the read-back as a DORMANT ``@wrap_tool_call`` middleware
(``src/deep_runtime/middleware/readback.py``, unit-proven in ``test_readback_middleware.py``),
a trust-increment helper (``trust_increment.py``), and the chain wiring in
``AgentInvoker._build_deep_agent_for`` (read_back flag-gated behind ``deep_readback_enabled``,
INNER of write_lock, OUTER of the muldro_tool_dispatcher). This test proves the INTEGRATION —
that read_back is actually in the REAL wired chain and annotates its verdict through the REAL
``stream_deep_agent_events`` SSE adapter, forced on via ``deep_readback_enabled=True`` — NOT the
verdict/annotation SEMANTICS (already unit-proven in Phase 1/2; not duplicated here).

Drives the REAL chain, fully OFFLINE (no Anthropic API, no Postgres, no Redis):

    AgentInvoker._build_deep_agent_for   (installs the full gated middleware chain +, when
                                          deep_readback_enabled, the read_back hop)
      -> build_deep_agent / create_deep_agent
      -> stream_deep_agent_events         (the frozen SSE adapter)

A scripted-fake LEAD model emits ONE tool call to a stub Muldro tool (``email_send``) whose
registry capability is ``email.send`` — an IRREVERSIBLE write in ``UNVERIFIABLE_CAPABILITIES``.
The lead carries ``email.send`` in its capability_scope so the capability_scope guard ADMITS the
call; ``authorization_source="direct_user_request"`` keeps the trust_gate dormant (short-circuits
BEFORE any interrupt/DB — deterministic offline). The dispatcher's recording ``execute_tool``
returns a JSON success (``{"message_id": "m1"}``). With the wired ``read_fn=None`` the read-back
resolves to **UNVERIFIED** (email.send is statically irreversible AND has no post-condition read).

Positive guard (``deep_readback_enabled=True``):
  * the ``email_send`` ``tool_result`` frame's ``result`` JSON carries
    ``verification.verdict == "unverified"``;
  * the frame is NOT blocked (``blocked is False``) and the original ``message_id`` is preserved;
  * the frozen SSE frame set is intact (agent_start … agent_done, no error frame).

NEGATIVE CONTROL NC-A (flag gate, WITH TEETH): the SAME drive with
``deep_readback_enabled=False`` produces a ``tool_result`` frame with NO ``verification`` key —
read_back is simply not in the chain. Reproduction teeth (run out-of-band, not in this file):
forcing ``if self._settings.deep_readback_enabled:`` → ``if True:`` in agent_invoker.py makes
NC-A FAIL (the annotation appears with the flag off), proving the flag-gate dormancy is real.

Risk determinism: the read-back middleware calls the invoker's ``_assess_risk`` (which wraps
``get_or_assess_risk``). We patch ``get_or_assess_risk`` to a fixed high/irreversible
``RiskAssessment`` so the drive never touches a live API — email.send is statically irreversible
regardless, so the UNVERIFIED verdict is deterministic either way.

Skipped (by design): the budget ``TokenUsage`` persistence + the unavailable_server
short-circuit are NOT asserted here — the budget/unavailable factories have their own dedicated
unit tests (``test_budget.py`` / ``test_unavailable_server.py``) and asserting them would need
real-Postgres / auth-envelope scaffolding out of scope for a read-back guard.
"""

from __future__ import annotations

import json
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
from src.deep_runtime.prompt_bridge import build_system_message
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from src.services.risk_assessor import RiskAssessment
from tests.conftest import make_mock_settings

WS = "ws_readback_e2e"
USER = "user_readback_e2e"
MODEL_ID = "claude-sonnet-4-6"

# email_send -> email.send: an IRREVERSIBLE write in UNVERIFIABLE_CAPABILITIES. With read_fn=None
# the read-back resolves to UNVERIFIED (statically irreversible, no post-condition read seam).
_NAME_TO_CAP: dict[str, str | None] = {"email_send": "email.send"}
_TOOL_DEFS = [{"name": "email_send", "description": "send an email"}]

CAP_SCOPE_TOOL_REGISTRY = "src.deep_runtime.middleware.capability_scope.ToolRegistry"
TRUST_GATE_TOOL_REGISTRY = "src.deep_runtime.middleware.trust_gate.ToolRegistry"
GET_OR_ASSESS_RISK = "src.services.risk_assessor.get_or_assess_risk"

# The static high/irreversible risk the read-back's assess_risk resolves to offline. email.send is
# statically irreversible regardless of this value; pinning it just makes the drive API-free.
_STUB_RISK = RiskAssessment(
    risk_level="high",
    reasoning="offline e2e stub",
    reversible=False,
    blast_radius="external_single",
)


# ---------------------------------------------------------------------------
# Offline capability resolution: stub ToolRegistry with a name->ToolDef map.
# The fake ToolDef exposes every attr the gated chain reads directly:
#   .capability (capability_scope / trust_gate / write_lock / read_back),
#   .enabled    (governor_audit's disabled-tool block — a DIRECT attr, not getattr),
#   .server / .risk_level are read via getattr(default) so they are belt-and-braces.
# ---------------------------------------------------------------------------
class _FakeToolDef:
    def __init__(self, capability: str | None) -> None:
        self.capability = capability
        self.enabled = True
        self.server = None
        self.risk_level = "high"


class _FakeRegistry:
    def __init__(self, db: Any, workspace_id: str | None = None) -> None:  # noqa: ARG002
        pass

    async def get_tool(self, name: str) -> _FakeToolDef | None:
        cap = _NAME_TO_CAP.get(name)
        return _FakeToolDef(cap) if cap is not None else None


def _fake_db_factory():
    """An async-context-manager factory yielding a sentinel DB (matches test_delegate_e2e)."""

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _write_recorder(rec: list[tuple[str, dict]]):
    """A fake ``execute_tool(name, args, user_id, workspace_id)`` recording each call and
    returning a JSON-success write result the dispatcher wraps into a status='success'
    ToolMessage (no ``error``/``blocked`` key)."""

    async def _execute(name: str, args: dict, user_id: str, workspace_id: str) -> dict:  # noqa: ARG001
        rec.append((name, args))
        return {"message_id": "m1"}

    return _execute


# ---------------------------------------------------------------------------
# Scripted streaming fake chat model — turn chosen by inbound ToolMessage count.
# Forces provider="anthropic" + carries model_name so deepagents' harness-profile key
# resolves to ``anthropic:<model_name>`` exactly as a real ChatAnthropic would.
# Copied from the delegate-e2e / subagent_gated_probe harness.
# ---------------------------------------------------------------------------
class ScriptedModel(BaseChatModel):
    model_name: str = MODEL_ID

    _turns: list[list[AIMessageChunk]]

    def __init__(self, turns: list[list[AIMessageChunk]], model_name: str = MODEL_ID) -> None:
        super().__init__(model_name=model_name)
        object.__setattr__(self, "_turns", turns)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake-readback-e2e"

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


def _lead_write_turns() -> list[list[AIMessageChunk]]:
    """Lead turn 0 calls the irreversible write ``email_send``; turn 1 answers with text."""
    return [
        [
            AIMessageChunk(content=[{"type": "text", "text": "Sending the email.", "index": 0}]),
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="email_send",
                        args=json.dumps({"to": "a@b.com", "body": "hi"}),
                        id="lead_write_tc",
                        index=1,
                    )
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": "Done — email sent.", "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


def _lead() -> SubAgent:
    """The deep LEAD carrying ``email.send`` in scope so the capability_scope guard ADMITS the
    write. A distinct name (``presenter``) matches the delegate-e2e lead dispatch convention."""
    return SubAgent(
        name="presenter",
        prompt="You are the lead.",
        model_tier="sonnet",
        capability_scope={"email.send"},
    )


def _make_invoker(*, deep_readback_enabled: bool) -> AgentInvoker:
    """A real AgentInvoker (runtime=deep) wired for the OFFLINE drive.

    ``services=None`` -> every ``services.extras.get("redis")`` guard resolves to None (no Redis,
    so write_lock short-circuits as a no-op). ``client`` is an unused MagicMock (delegates OFF;
    risk is patched). ``db_factory_provider`` yields the fake factory the gated chain resolves
    tool defs through (patched ToolRegistry)."""
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])
    tool_executor.execute_tool = _write_recorder([])  # replaced per-test below

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    lead = _lead()
    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", deep_readback_enabled=deep_readback_enabled),
        client=MagicMock(),
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
    invoker: AgentInvoker, lead: SubAgent, *, thread_id: str
) -> list[dict]:
    """Build the deep lead via the REAL ``_build_deep_agent_for`` (with the ``email_send`` shell)
    and collect the adapter frames. ``authorization_source="direct_user_request"`` keeps the
    trust_gate dormant so the write executes inline with no interrupt."""
    deep_agent = await invoker._build_deep_agent_for(
        lead,
        _TOOL_DEFS,
        user_id=USER,
        workspace_id=WS,
        thread_id=thread_id,
        authorization_source="direct_user_request",
        system_prompt=build_system_message(invoker.build_system_prompt(lead, "")),
        subagents=(),
        presence="absent",
    )
    return [
        frame
        async for frame in stream_deep_agent_events(
            deep_agent,
            {"messages": [{"role": "user", "content": "email a@b.com"}]},
            {"configurable": {"thread_id": thread_id}},
            agent_name="presenter",
            model=MODEL_ID,
        )
    ]


def _tool_result_frame(frames: list[dict], tool: str) -> dict | None:
    return next((f for f in frames if f["event"] == "tool_result" and f["tool"] == tool), None)


# ═══════════════════════════════════════════════════════════════════════════════
# THE ONE CRITICAL HAZARD: process-global _HARNESS_PROFILES restore (copied verbatim from
# test_delegate_e2e.py). Defensive here — this test never calls disable_general_purpose_subagent
# — but create_deep_agent registers the lead's ``anthropic:claude-sonnet-4-6`` profile, so we
# snapshot + RESTORE it so nothing leaks into the wider sonnet-lead deep suite.
# ═══════════════════════════════════════════════════════════════════════════════
_GP_TEST_KEYS = ("anthropic:claude-sonnet-4-6", "anthropic:claude-opus-4-8")


@pytest.fixture(autouse=True)
def _restore_harness_profiles():
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


@pytest.fixture(autouse=True)
def _stub_unavailable_server_registry():
    """The unavailable_server breaker (OUTER of trust_gate in the gated chain) resolves each
    tool's MCP server via ``ToolRegistry.get_tool`` on every tool call. Offline, stub it with the
    SAME fake registry so no real DB session is touched — the fake ToolDef's ``server`` is None,
    so the breaker never short-circuits (a pure no-op here)."""
    with patch("src.deep_runtime.middleware.unavailable_server.ToolRegistry", _FakeRegistry):
        yield


# ═══════════════════════════════════════════════════════════════════════════════
# POSITIVE GUARD — forced-on read_back annotates the irreversible write UNVERIFIED, in the
# REAL chain, through the REAL SSE adapter, without blocking or dropping the tool output.
# ═══════════════════════════════════════════════════════════════════════════════
async def test_forced_on_readback_annotates_unverified_through_real_chain():
    rec: list = []
    lead_fake = ScriptedModel(_lead_write_turns())
    invoker = _make_invoker(deep_readback_enabled=True)
    invoker._tool_executor.execute_tool = _write_recorder(rec)
    lead = invoker._agents["presenter"]

    with (
        patch.object(agent_builder, "build_chat_model", AsyncMock(return_value=lead_fake)),
        patch(CAP_SCOPE_TOOL_REGISTRY, _FakeRegistry),
        patch(TRUST_GATE_TOOL_REGISTRY, _FakeRegistry),
        patch(GET_OR_ASSESS_RISK, AsyncMock(return_value=_STUB_RISK)),
    ):
        frames = await _build_lead_and_stream(invoker, lead, thread_id="e2e-readback-on")

    # The write actually executed (dispatcher reached the recording execute_tool) — the read-back
    # is post-write, so a missing execution would make the whole assertion vacuous.
    assert ("email_send", {"to": "a@b.com", "body": "hi"}) in rec, (
        f"the irreversible write never reached execute_tool; rec={rec}"
    )

    # The read-back annotated the tool_result: verdict UNVERIFIED, NOT blocked, message_id kept.
    tr = _tool_result_frame(frames, "email_send")
    assert tr is not None, f"no email_send tool_result; events={[f['event'] for f in frames]}"
    assert tr["blocked"] is False, "an UNVERIFIED read-back must annotate content, never block"
    body = json.loads(tr["result"])
    assert isinstance(body, dict), f"tool_result did not parse to a dict; result={tr['result']!r}"
    assert body.get("verification", {}).get("verdict") == "unverified", (
        f"read-back verdict not annotated onto the tool_result; body={body!r}"
    )
    assert body.get("message_id") == "m1", (
        f"original tool output not preserved under the annotation; body={body!r}"
    )

    # The frozen SSE contract survives (mirrors the delegate-e2e frame-shape assertions).
    events = [f["event"] for f in frames]
    assert events[0] == "agent_start"
    assert "agent_done" in events
    assert "tool_call" in events
    assert "tool_result" in events
    assert not any(f["event"] == "error" for f in frames), (
        f"unexpected error frame(s): {[f for f in frames if f['event'] == 'error']}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# NEGATIVE CONTROL NC-A (flag gate, teeth) — with deep_readback_enabled=False the read_back hop
# is NOT in the chain, so the SAME irreversible write's tool_result carries NO ``verification``
# key. Reproduction: forcing ``if True:`` in agent_invoker.py makes THIS assertion FAIL (the
# annotation appears with the flag off) — proving the flag-gate dormancy is real.
# ═══════════════════════════════════════════════════════════════════════════════
async def test_neg_control_flag_off_no_verification_annotation():
    rec: list = []
    lead_fake = ScriptedModel(_lead_write_turns())
    invoker = _make_invoker(deep_readback_enabled=False)
    invoker._tool_executor.execute_tool = _write_recorder(rec)
    lead = invoker._agents["presenter"]

    with (
        patch.object(agent_builder, "build_chat_model", AsyncMock(return_value=lead_fake)),
        patch(CAP_SCOPE_TOOL_REGISTRY, _FakeRegistry),
        patch(TRUST_GATE_TOOL_REGISTRY, _FakeRegistry),
        patch(GET_OR_ASSESS_RISK, AsyncMock(return_value=_STUB_RISK)),
    ):
        frames = await _build_lead_and_stream(invoker, lead, thread_id="e2e-readback-off")

    # The write still executes — only the read-back annotation is absent.
    assert ("email_send", {"to": "a@b.com", "body": "hi"}) in rec, (
        f"the write never reached execute_tool; rec={rec}"
    )
    tr = _tool_result_frame(frames, "email_send")
    assert tr is not None, f"no email_send tool_result; events={[f['event'] for f in frames]}"
    assert tr["blocked"] is False
    body = json.loads(tr["result"])
    # THE TEETH: with the flag OFF read_back is not wired, so there is NO verification key —
    # while the raw write output is delivered untouched.
    assert "verification" not in body, (
        f"verification annotation present with the flag OFF; body={body!r}"
    )
    assert body.get("message_id") == "m1", f"raw write output missing; body={body!r}"
