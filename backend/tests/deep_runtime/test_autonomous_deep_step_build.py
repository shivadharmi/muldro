"""Step 10C P1a: the autonomous deep-step executor build — ledger-in-deep-chain
(the tenant-safety crux) + the SQ2 Branch-C gate short-circuit.

Two changes are locked here:

* ``make_trust_gate_middleware(..., pre_approved_capabilities=frozenset())`` — a
  capability already gated at the STEP level (dag_runner's durable TrustEngine gate)
  is passed through the deep tool-call gate WITHOUT re-prompting, while an UN-approved
  within-step capability expansion STILL gates (SQ2 Branch C, spike 0.3). Default-empty
  keeps every existing caller byte-identical.
* ``AgentInvoker.run_autonomous_deep_step`` — the autonomous step seam: wraps the
  dispatcher's ``execute_tool`` with the REAL per-step idempotency ledger so LangGraph's
  at-least-once replay fires each external write EXACTLY ONCE (the deep chain otherwise
  wires the dispatcher to the RAW executor → double-fire on resume), builds the deep
  agent with ``authorization_source=AUTONOMOUS`` + the Branch-C set, and maps the
  terminal stream result to the SAME step-output dict the legacy path produces.

Fast unit tests drive the gate via ``mw.awrap_tool_call`` (no graph) with ``interrupt``
patched, and the build/output-shape tests stub ``_build_deep_agent_for`` +
``stream_deep_agent_events`` (no real deep build). The ledger CRUX + read-bypass tests
need the REAL ledger + registry, so they use real Postgres (guarded, NullPool, seeded
FK chain, teardown).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from src.deep_runtime.authorization import AuthorizationSource
from src.deep_runtime.middleware.trust_gate import make_trust_gate_middleware
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.services.risk_assessor import RiskAssessment
from tests.conftest import make_mock_settings

GATE_MODULE = "src.deep_runtime.middleware.trust_gate"
INVOKER_MODULE = "src.orchestrator.agent_invoker"
USER_ID = "u_test"
WORKSPACE_ID = "ws_test"


# ─────────────────────────── shared test doubles ────────────────────────────


def _request(tool_name: str, args: dict | None = None, call_id: str = "call_1"):
    """Minimal ToolCallRequest stand-in: only ``.tool_call`` is read."""
    return SimpleNamespace(tool_call={"name": tool_name, "args": args or {}, "id": call_id})


def _hook(mw):
    """The async wrap-tool-call hook bound on the middleware instance."""
    return mw.awrap_tool_call


def _persist_db_factory(existing=None):
    """A db_factory whose session backs the find/decide/persist blocks.

    ``.execute(...).scalars().first()`` resolves to *existing* (default ``None`` so
    the idempotent get-or-create takes the create branch); ``.commit`` is an AsyncMock.
    """

    @asynccontextmanager
    async def _factory():
        db = MagicMock(name="persist-db")
        result = MagicMock(name="execute-result")
        result.scalars.return_value.first.return_value = existing
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.add = MagicMock()
        yield db

    return _factory


def _bc_gate(*, pre_approved, db_factory, assess_risk, thread_id="t_bc"):
    """Build the deep trust_gate with the Branch-C ``pre_approved_capabilities`` param."""
    return make_trust_gate_middleware(
        authorization_source=AuthorizationSource.AUTONOMOUS,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        thread_id=thread_id,
        agent_name="executor",
        db_factory=db_factory,
        assess_risk=assess_risk,
        pre_approved_capabilities=pre_approved,
    )


def _fake_create_approval(approval_id="apr_x"):
    """A patched ``create_approval`` returning a stub with the given id."""
    return AsyncMock(return_value=SimpleNamespace(approval_id=approval_id))


def _send_email_tool() -> dict:
    """A minimal tool-shell dict (name/description/input_schema) for the offline tests."""
    return {"name": "send_email", "description": "d", "input_schema": {}}


def _gating_risk() -> RiskAssessment:
    return RiskAssessment(
        risk_level="high",
        reasoning="gating write (high, irreversible)",
        reversible=False,
        blast_radius="external_single",
    )


class _FakeModel(BaseChatModel):
    """Deterministic no-API model: turn 1 emits a tool_call to ``tool_name``; the
    resumed turn (after it sees a ToolMessage) emits a final ``done`` text chunk."""

    def __init__(self, tool_name: str, args: dict, **kw: Any) -> None:
        super().__init__(**kw)
        object.__setattr__(self, "_tool_name", tool_name)
        object.__setattr__(self, "_args", args)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _script(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return [AIMessageChunk(content=[{"type": "text", "text": "done", "index": 0}])]
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name=self._tool_name,
                        args=json.dumps(self._args),
                        id="call_step_1",
                        index=0,
                    )
                ],
            )
        ]

    async def _astream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        for ch in self._script(messages):
            yield ChatGenerationChunk(message=ch)

    async def _agenerate(
        self,
        messages,
        stop=None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> ChatResult:
        merged: AIMessageChunk | None = None
        async for gen in self._astream(messages):
            merged = gen.message if merged is None else merged + gen.message
        assert merged is not None
        msg = AIMessage(content=merged.content, tool_calls=list(merged.tool_calls))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, *a: Any, **k: Any) -> ChatResult:
        raise NotImplementedError


# ══════════════════════════ Test 1 — Branch-C gate ══════════════════════════


async def test_branch_c_preapproved_capability_short_circuits():
    """A capability in ``pre_approved_capabilities`` passes THROUGH the deep gate — no
    risk assessment, no trust evaluation, no interrupt: the step's already-step-gated
    write is not double-prompted."""
    handler = AsyncMock(return_value=ToolMessage(content="executed", tool_call_id="c1"))
    assess_risk = AsyncMock(return_value=_gating_risk())
    mock_interrupt = MagicMock(return_value="approve")
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="approval_required", justification="risky")
    )

    mw = _bc_gate(
        pre_approved=frozenset({"email.send"}),
        db_factory=_persist_db_factory(existing=None),
        assess_risk=assess_risk,
    )
    with (
        patch(f"{GATE_MODULE}._resolve_capability", AsyncMock(return_value=(True, "email.send"))),
        patch(f"{GATE_MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{GATE_MODULE}.create_approval", _fake_create_approval()),
        patch(f"{GATE_MODULE}.interrupt", mock_interrupt),
    ):
        result = await _hook(mw)(_request("send_email", {"to": "x"}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    assess_risk.assert_not_awaited()  # short-circuit: no risk assessment
    mock_interrupt.assert_not_called()  # short-circuit: never gated
    fake_te.evaluate.assert_not_awaited()  # short-circuit: no trust evaluation


async def test_branch_c_empty_preapproved_still_gates():
    """With the default-empty ``pre_approved_capabilities`` the SAME write reaches the
    gate — risk assessed, Approval persisted, ``interrupt()`` called (byte-neutral for
    every existing caller)."""
    handler = AsyncMock(return_value=ToolMessage(content="executed", tool_call_id="c1"))
    assess_risk = AsyncMock(return_value=_gating_risk())
    mock_interrupt = MagicMock(return_value="approve")
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="approval_required", justification="risky")
    )

    mw = _bc_gate(
        pre_approved=frozenset(),
        db_factory=_persist_db_factory(existing=None),
        assess_risk=assess_risk,
    )
    with (
        patch(f"{GATE_MODULE}._resolve_capability", AsyncMock(return_value=(True, "email.send"))),
        patch(f"{GATE_MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{GATE_MODULE}.create_approval", _fake_create_approval()),
        patch(f"{GATE_MODULE}.interrupt", mock_interrupt),
    ):
        result = await _hook(mw)(_request("send_email", {"to": "x"}, "c1"), handler)

    mock_interrupt.assert_called_once()  # gated — NOT short-circuited
    assess_risk.assert_awaited_once()
    handler.assert_awaited_once()  # runs only after the approve verdict
    assert result is handler.return_value


async def test_branch_c_unapproved_capability_still_gates():
    """A DIFFERENT capability (payment.send) NOT in the pre-approved set STILL gates even
    when email.send IS pre-approved — proving the short-circuit is a set membership, not a
    dead-wire that opens all writes."""
    handler = AsyncMock(return_value=ToolMessage(content="executed", tool_call_id="c1"))
    assess_risk = AsyncMock(return_value=_gating_risk())
    mock_interrupt = MagicMock(return_value="approve")
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="approval_required", justification="risky")
    )

    mw = _bc_gate(
        pre_approved=frozenset({"email.send"}),  # payment.send is NOT pre-approved
        db_factory=_persist_db_factory(existing=None),
        assess_risk=assess_risk,
    )
    with (
        patch(f"{GATE_MODULE}._resolve_capability", AsyncMock(return_value=(True, "payment.send"))),
        patch(f"{GATE_MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{GATE_MODULE}.create_approval", _fake_create_approval("apr_y")),
        patch(f"{GATE_MODULE}.interrupt", mock_interrupt),
    ):
        await _hook(mw)(_request("send_payment", {"amount": 100}, "c1"), handler)

    mock_interrupt.assert_called_once()  # un-approved capability STILL gated
    assess_risk.assert_awaited_once()


# ═══════════════ Test 3 — provenance (offline: stubbed build) ════════════════


def _make_offline_invoker() -> AgentInvoker:
    """AgentInvoker whose ``_build_deep_agent_for`` is stubbed (no real deep build).
    Used by the provenance + output-shape tests — no DB, no LangGraph."""
    tool_executor = MagicMock()
    tool_executor.execute_tool = AsyncMock(return_value={"status": "sent"})
    inv = AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: MagicMock(),
        tool_executor=tool_executor,
        context=MagicMock(),
        agents={},
        checkpointer_provider=lambda: None,
    )
    # Offline: resolve the streaming/budget model id without a real DB (mirror the
    # catalog default) so tests don't drive ModelResolver against the mock session.
    inv._resolved_model_id = AsyncMock(side_effect=lambda agent, ws: inv.get_model_for_agent(agent))
    return inv


def _executor_agent() -> SubAgent:
    return SubAgent(
        name="executor",
        prompt="exec",
        model_tier="haiku",
        capability_scope={"email.send", "email.read"},
        thinking=ThinkingConfig(enabled=False),
    )


def _done_stream(**extra):
    async def _gen(*a, **k):
        yield {"event": "agent_start", "agent": "executor", "model": None}
        for frame in extra.get("pre", ()):
            yield frame
        yield {
            "event": "agent_done",
            "agent": "executor",
            "text": extra.get("text", "done"),
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "tools_called": extra.get("tools_called", ["send_email"]),
            "latency_ms": 1,
            "cost_usd": 0.0,
        }

    return _gen


async def _run_offline(inv: AgentInvoker, *, pre_approved, tools=None) -> dict:
    return await inv.run_autonomous_deep_step(
        executor=_executor_agent(),
        tools=tools if tools is not None else [_send_email_tool()],
        message="send it",
        context_block="",
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        run_id="run_1",
        step_id="s1",
        pre_approved_capabilities=pre_approved,
    )


async def test_run_autonomous_deep_step_streams_workspace_resolved_model():
    """R1 (autonomous path): agent_start.model must be the workspace-resolved model id
    (via _resolved_model_id), not the deployment-global settings.resolved_model that
    step_runner used to thread in."""
    inv = _make_offline_invoker()
    inv._build_deep_agent_for = AsyncMock(return_value=object())
    inv._resolved_model_id = AsyncMock(return_value="gpt-5")

    captured: dict[str, object] = {}
    stream = _done_stream()

    def _capture(*a, **k):
        captured["model"] = k.get("model")
        return stream(*a, **k)

    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _capture):
        await _run_offline(inv, pre_approved=frozenset({"email.send"}))

    assert captured["model"] == "gpt-5"
    inv._resolved_model_id.assert_awaited()


async def test_run_autonomous_deep_step_uses_autonomous_provenance():
    """The autonomous step seam builds the deep agent with the literal
    ``AuthorizationSource.AUTONOMOUS`` provenance, forwards the Branch-C set, and injects
    a ledger-wrapped ``execute_tool`` adapter (never the raw executor) through the 10B seam."""
    inv = _make_offline_invoker()
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _done_stream()):
        await _run_offline(inv, pre_approved=frozenset({"email.send"}))

    inv._build_deep_agent_for.assert_awaited_once()
    call = inv._build_deep_agent_for.call_args
    assert call.args[0] is not None and call.args[0].name == "executor"  # executor positional
    assert call.kwargs["authorization_source"] == AuthorizationSource.AUTONOMOUS
    assert call.kwargs["pre_approved_capabilities"] == frozenset({"email.send"})
    # execute_tool is the ledger adapter (a fresh closure), NOT the raw tool_executor fn.
    execute_tool = call.kwargs["execute_tool"]
    assert callable(execute_tool)
    assert execute_tool is not inv._tool_executor.execute_tool


# ════════════════ Test 4 — output shape (offline: stubbed build) ═════════════


async def test_run_autonomous_deep_step_output_shape_happy_path():
    """The happy-path dict has EXACTLY the legacy step-output keys and status=completed —
    byte-shape-identical to ``run_step_via_agent_loop``."""
    inv = _make_offline_invoker()
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    tool_result = {
        "event": "tool_result",
        "agent": "executor",
        "tool": "send_email",
        "result": json.dumps({"status": "sent"}),
        "blocked": False,
        "latency_ms": 1,
    }
    with patch(
        f"{INVOKER_MODULE}.stream_deep_agent_events",
        _done_stream(pre=(tool_result,), text="all done", tools_called=["send_email"]),
    ):
        out = await _run_offline(inv, pre_approved=frozenset({"email.send"}))

    assert set(out.keys()) == {"status", "result", "tools_called", "errors"}
    assert out["status"] == "completed"
    assert out["result"] == "all done"
    assert out["tools_called"] == ["send_email"]
    assert out["errors"] == []


async def test_run_autonomous_deep_step_auth_required_passthrough():
    """A tool returning the ``auth_required`` envelope is surfaced with the EXACT legacy
    passthrough keys (status=error, error_code, provider, server, auth_required) so
    ``dag_runner._defer_for_reauth`` parks the run for re-auth."""
    inv = _make_offline_invoker()
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    auth_envelope = {
        "status": "error",
        "error_code": "auth_required",
        "provider": "google",
        "server": "google_workspace",
    }
    tool_result = {
        "event": "tool_result",
        "agent": "executor",
        "tool": "send_email",
        "result": json.dumps(auth_envelope),
        "blocked": True,
        "latency_ms": 1,
    }
    with patch(
        f"{INVOKER_MODULE}.stream_deep_agent_events",
        _done_stream(pre=(tool_result,), text="", tools_called=["send_email"]),
    ):
        out = await _run_offline(inv, pre_approved=frozenset({"email.send"}))

    assert out["status"] == "error"
    assert out["error_code"] == "auth_required"
    assert out["provider"] == "google"
    assert out["server"] == "google_workspace"
    assert out["auth_required"]["error_code"] == "auth_required"
    # base keys always present
    assert {"status", "result", "tools_called", "errors"} <= set(out.keys())


async def test_run_autonomous_deep_step_refuses_empty_workspace():
    """LOW-1 (fail-closed): an empty ``workspace_id`` is refused up front — the deep agent is
    NEVER built and no write path is entered. The A6 guard degenerates to ``"" == ""`` for an
    empty tenant, so this explicit check is the tenant fail-closed CLAUDE.md requires."""
    inv = _make_offline_invoker()
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _done_stream()):
        out = await inv.run_autonomous_deep_step(
            executor=_executor_agent(),
            tools=[_send_email_tool()],
            message="send it",
            context_block="",
            user_id=USER_ID,
            workspace_id="",  # empty tenant → must be refused BEFORE any build/write
            run_id="run_1",
            step_id="s1",
            pre_approved_capabilities=frozenset({"email.send"}),
        )

    assert out["status"] == "error"
    assert out["errors"] == ["missing workspace_id"]
    assert set(out.keys()) == {"status", "result", "tools_called", "errors"}
    inv._build_deep_agent_for.assert_not_awaited()  # refused before any deep build


# ═══════════ Test 2 — ledger-in-deep CRUX (real Postgres, guarded) ═══════════


def _db_reachable() -> bool:
    import asyncpg

    from src.config.settings import get_settings

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover
        return False


_DB_OK = _db_reachable()


@asynccontextmanager
async def _db_env():
    """Real-DB env: NullPool engine + User→Workspace seed; teardown cascades tool defs +
    idempotency_ledger via the workspace FK ON DELETE CASCADE."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from ulid import ULID

    from src.config.settings import get_settings
    from src.models.users import User, Workspace

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"10c-{suffix}@example.com", display_name="10c"))
            db.add(Workspace(workspace_id=workspace_id, name="10c-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def _seed_tool(factory, workspace_id, *, name, capability, requires_approval):
    from ulid import ULID

    from src.models.tool_definitions import ToolBackend, ToolDefinition

    async with factory() as db:
        db.add(
            ToolDefinition(
                tool_id=f"tool_{ULID()}",
                workspace_id=workspace_id,
                name=name,
                description=f"seed {name}",
                capability=capability,
                requires_approval=requires_approval,
                backend=ToolBackend.INTERNAL_MCP,
                enabled=True,
            )
        )
        await db.commit()


def _make_db_invoker(factory) -> tuple[AgentInvoker, list[str]]:
    """AgentInvoker wired to the real ``factory`` + a fake tool executor that records
    each external effect into a list (so double-fire is observable)."""
    effects: list[str] = []

    class _FakeExecutor:
        async def execute_tool(self, name, args, user_id, workspace_id):  # noqa: ARG002
            effects.append(name)
            return {"status": "sent", "tool": name}

    inv = AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: factory,
        tool_executor=_FakeExecutor(),
        context=MagicMock(),
        agents={"executor": _executor_agent()},
        checkpointer_provider=lambda: None,
    )
    return inv, effects


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_ledger_dedups_write_across_runs():
    """THE CRUX: the write's ``execute_tool`` is ledger-wrapped, so a SECOND
    ``run_autonomous_deep_step`` on the same (run_id, step_id) with identical args fires
    the external effect ZERO more times (already_done), and a completed ledger row exists.
    Proves LangGraph's at-least-once replay is deduped to exactly-once."""
    from sqlalchemy import select

    from src.models.idempotency_ledger import IdempotencyLedgerEntry

    async with _db_env() as (factory, ws, uid):
        await _seed_tool(
            factory, ws, name="send_email", capability="email.send", requires_approval=True
        )
        inv, effects = _make_db_invoker(factory)

        write_tool = {
            "name": "send_email",
            "description": "send an email",
            "input_schema": {
                "type": "object",
                "properties": {"to": {"type": "string"}, "subject": {"type": "string"}},
            },
        }

        async def _run_once() -> dict:
            with patch(
                "src.deep_runtime.agent_builder.build_chat_model",
                AsyncMock(
                    return_value=_FakeModel("send_email", {"to": "f@x.com", "subject": "hi"})
                ),
            ):
                return await inv.run_autonomous_deep_step(
                    executor=_executor_agent(),
                    tools=[write_tool],
                    message="send it",
                    context_block="",
                    user_id=uid,
                    workspace_id=ws,
                    run_id="run_dedup",
                    step_id="s1",
                    pre_approved_capabilities=frozenset({"email.send"}),
                )

        out1 = await _run_once()
        out2 = await _run_once()

        assert out1["status"] == "completed"
        assert out2["status"] == "completed"
        # The external effect fired EXACTLY ONCE across both runs (ledger dedup).
        assert effects == ["send_email"], f"double-fire: {effects}"

        async with factory() as db:
            rows = (
                (
                    await db.execute(
                        select(IdempotencyLedgerEntry).where(
                            IdempotencyLedgerEntry.workspace_id == ws
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].capability == "email.send"
        assert rows[0].status == "completed"


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_read_capability_bypasses_ledger():
    """A READ capability BYPASSES the ledger entirely — the read executes but NO
    IdempotencyLedgerEntry row is reserved for it (reads are side-effect free)."""
    from sqlalchemy import select

    from src.models.idempotency_ledger import IdempotencyLedgerEntry

    async with _db_env() as (factory, ws, uid):
        await _seed_tool(
            factory, ws, name="read_email", capability="email.read", requires_approval=False
        )
        inv, effects = _make_db_invoker(factory)

        read_tool = {
            "name": "read_email",
            "description": "read email",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
        with patch(
            "src.deep_runtime.agent_builder.build_chat_model",
            AsyncMock(return_value=_FakeModel("read_email", {"q": "investor"})),
        ):
            out = await inv.run_autonomous_deep_step(
                executor=_executor_agent(),
                tools=[read_tool],
                message="read it",
                context_block="",
                user_id=uid,
                workspace_id=ws,
                run_id="run_read",
                step_id="s1",
                pre_approved_capabilities=frozenset(),
            )

        assert out["status"] == "completed"
        assert effects == ["read_email"]  # the read executed

        async with factory() as db:
            rows = (
                (
                    await db.execute(
                        select(IdempotencyLedgerEntry).where(
                            IdempotencyLedgerEntry.workspace_id == ws
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []  # read never touched the ledger
