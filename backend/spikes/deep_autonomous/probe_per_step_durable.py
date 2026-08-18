"""Spike probe (Step 10C Phase 0.1 — SQ1 plan-killer): per-step REAL build_deep_agent
durable resume + exactly-once via the REAL IdempotencyLedger.

Extends the Step-1 proof (spikes/postgres_saver/probe.py — a minimal hand-written
StateGraph) to a REAL deep agent built via ``build_deep_agent`` with the real
capability-scope guard + the real ``muldro_tool_dispatcher`` wired to a
ledger-wrapped adapter, compiled under a REAL ``AsyncPostgresSaver`` on a
``make_thread_id(workspace_id)`` thread.

The make-or-break question (SQ1): a per-step react loop that is KILLED
mid-tool-call, then RESUMED on the same thread_id via ``ainvoke(None, cfg,
durability="sync")``, must fire its external write EXACTLY ONCE — because the
real idempotency ledger dedups LangGraph's mandatory at-least-once replay.

Crash model used = MODEL 1 (target): the write tool's effect fires + the ledger
records success, THEN the dispatcher adapter raises a hard ``RuntimeError`` on the
FIRST pass only (module-level ``CRASHED`` sentinel), simulating a process kill
AFTER the effect + record but BEFORE LangGraph checkpoints the tool node. On
resume the ledger row is ``completed`` → ``already_done`` → the effect is NOT
re-fired → the tool node returns cleanly → the agent completes.

Offline pre-flight (spikes scratch) confirmed the deep agent's ToolNode does NOT
swallow a ``RuntimeError`` raised from a ``wrap_tool_call`` middleware into a
``ToolMessage(status="error")`` — it propagates as a node failure, leaving a
resumable checkpoint that ``ainvoke(None, cfg)`` replays. So MODEL 1 (clean
resume) is viable; MODEL 3 (crash-before-record → in_flight_conflict) was not
needed.

Also proven here:
  * SQ4: the build path supplies ``authorization_source=AUTONOMOUS`` and a gated
    chain (capability_scope + ledger-wrapped dispatcher + trust_gate[AUTONOMOUS])
    COMPILES with the checkpointer with no exception.
  * ``workspace_of_thread_id(thread_id) == workspace_id`` (ws-bound checkpoint id).
  * Persisted checkpoint blobs are non-pickle (msgpack/json).
  * A read-only capability BYPASSES the ledger (no reserve).

Run:
    uv run python -m spikes.deep_autonomous.probe_per_step_durable

Self-contained + re-runnable: seeds a UUID-suffixed User+Workspace FK chain, two
ToolDefinition rows (write + read), and a dedicated spike_effects table; tears
everything down (FK cascade + effect table + checkpoint rows for the thread) in a
finally block. Exploratory spike code — hence the module-level prints and broad
orchestration. It should still lint clean.
"""

from __future__ import annotations

import asyncio
import json
import pickle
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
from deepagents import create_deep_agent  # noqa: F401  (imported for parity / availability check)
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

import src.deep_runtime.agent_builder as agent_builder
from src.config.settings import get_settings
from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.authorization import AuthorizationSource
from src.deep_runtime.checkpointer import build_async_postgres_saver
from src.deep_runtime.middleware.muldro_tool_dispatcher import make_muldro_tool_dispatcher
from src.deep_runtime.middleware.trust_gate import make_trust_gate_middleware
from src.deep_runtime.thread_identity import make_thread_id, workspace_of_thread_id
from src.deep_runtime.tool_bridge import build_tool_shells
from src.models.tool_definitions import ToolBackend, ToolDefinition
from src.models.users import User, Workspace
from src.services.idempotency.ledger import IdempotencyLedger
from src.services.idempotency.wrapper import (
    IdempotencyContext,
    make_idempotent_execute_tool_fn,
)

# --- Connection strings -----------------------------------------------------
SETTINGS = get_settings()
SQLA_URL = SETTINGS.database_url  # postgresql+asyncpg://...
PSYCOPG_URL = SQLA_URL.replace("+asyncpg", "", 1)  # psycopg3 DSN (no +asyncpg)

WRITE_TOOL = "spike_write_email"
READ_TOOL = "spike_read_email"

# Module-level sentinels (survive within the process; the checkpoint in Postgres
# is what survives the simulated "restart"). Mirror postgres_saver/probe.py.
DISPATCH_CALLS: list[str] = []  # every adapter invocation of the WRITE tool
CRASHED: list[int] = []  # length 1 after the one-shot pass-1 crash fires
EFFECTS_FIRED: list[str] = []  # every real external effect (spike_effects INSERT)


# --------------------------------------------------------------------------- #
# Fake deterministic chat model (no API): turn 1 emits a tool_call to the write
# tool; turn 2 (after it sees a ToolMessage) emits a final text chunk.
# --------------------------------------------------------------------------- #
class _M(BaseChatModel):
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
                        name=WRITE_TOOL,
                        args=json.dumps({"to": "founder@example.com", "subject": "spike"}),
                        id="call_spike_1",
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


async def _reachable() -> bool:
    try:
        conn = await asyncpg.connect(dsn=PSYCOPG_URL)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return True
    except Exception as exc:  # noqa: BLE001 - probe: report and bail cleanly
        print(f"POSTGRES_UNREACHABLE: {exc!r}")
        return False


def _make_executor():
    """Minimal executor SubAgent whose capability_scope INCLUDES the write cap so
    build_deep_agent's fail-closed guard is satisfied and the capability_scope
    middleware allows the write tool. model_tier/thinking are irrelevant — the
    real ChatAnthropic is patched out for the deterministic _M fake below."""
    from src.orchestrator.agents import SubAgent, ThinkingConfig

    return SubAgent(
        name="executor",
        prompt="spike executor",
        model_tier="haiku",
        capability_scope={"email.send", "email.read"},
        max_tokens=1024,
        temperature=0.0,
        thinking=ThinkingConfig(enabled=False),
    )


def _fake_resolve_capability(tool_name: str, _db_factory, _workspace_id):
    """Injected into the ledger wrapper. The spike tools aren't in the real
    registry's capability map for the ledger's purposes, so we resolve them here.
    Read tool → is_write=False (bypasses the ledger); write tool → is_write=True."""

    async def _inner():
        if tool_name == READ_TOOL:
            return ("email.read", False)
        return ("email.send", True)

    return _inner()


def _build_effect_fn(factory):
    """The real external effect: an idempotent Postgres write (INSERT one
    spike_effects row) plus a 'sent' result. Signature matches
    ToolExecutor.execute_tool's inner contract (kw-only user_id/workspace_id)."""

    async def effect_fn(tool_name, tool_input, *, user_id, workspace_id):  # noqa: ARG001
        async with factory() as db:
            await db.execute(
                text("INSERT INTO spike_effects (id) VALUES (:id)"),
                {"id": f"eff_{ULID()}"},
            )
            await db.commit()
        EFFECTS_FIRED.append(tool_name)
        print(f"  [effect] fired external write (spike_effects INSERT) tool={tool_name}")
        return {"status": "sent", "tool": tool_name}


    return effect_fn


def _build_ledger_adapter(factory, workspace_id, run_id):
    """Compose the REAL ledger over the effect fn, then wrap it in the
    positional→keyword adapter the dispatcher calls. Built FRESH per pass (fresh
    ordinal counter) so it faithfully models a process restart."""
    ledger = IdempotencyLedger(factory)
    ctx = IdempotencyContext(
        ledger=ledger,
        run_id=run_id,
        step_id="s1",
        workspace_id=workspace_id,
        db_factory=factory,
    )
    idem_fn = make_idempotent_execute_tool_fn(
        _build_effect_fn(factory), ctx, resolve_capability=_fake_resolve_capability
    )

    async def adapter(name, args, user_id, workspace_id):  # positional (dispatcher contract)
        DISPATCH_CALLS.append(name)
        result = await idem_fn(name, args, user_id=user_id, workspace_id=workspace_id)
        # CRASH MODEL 1: kill AFTER the effect fired + ledger.record_success, BEFORE the
        # tool-node checkpoint commits. One-shot (pass 1 only). On resume the ledger row is
        # already 'completed' → idem_fn returned already_done above → effect NOT re-fired.
        if name == WRITE_TOOL and not CRASHED:
            CRASHED.append(1)
            print("  [crash] raising RuntimeError AFTER effect+record (pre-checkpoint kill)")
            raise RuntimeError("spike: simulated process kill after effect+record")
        return result

    return adapter


async def _build_agent(executor, factory, workspace_id, user_id, run_id, saver, *, gated=False):
    """Build a REAL deep agent via build_deep_agent. capability_scope is auto-installed
    (db_factory given); we append the muldro_tool_dispatcher wired to a fresh ledger
    adapter. When gated=True we ALSO append a trust_gate built with
    authorization_source=AUTONOMOUS (SQ4 compose-with-checkpointer proof)."""
    tool_shells = build_tool_shells(
        [
            {
                "name": WRITE_TOOL,
                "description": "send an email (spike write)",
                "input_schema": {
                    "type": "object",
                    "properties": {"to": {"type": "string"}, "subject": {"type": "string"}},
                },
            }
        ]
    )
    adapter = _build_ledger_adapter(factory, workspace_id, run_id)
    dispatcher = make_muldro_tool_dispatcher(
        execute_tool=adapter, user_id=user_id, workspace_id=workspace_id
    )
    extra = [dispatcher]
    if gated:

        async def _assess_risk(capability, tool_input):  # never invoked (compile-only)
            return None

        async def _resolve_for_gate(name):  # never invoked (compile-only)
            return (True, "email.send")

        extra.append(
            make_trust_gate_middleware(
                authorization_source=AuthorizationSource.AUTONOMOUS,
                workspace_id=workspace_id,
                user_id=user_id,
                thread_id=make_thread_id(workspace_id),
                agent_name="executor",
                db_factory=factory,
                assess_risk=_assess_risk,
                resolve_capability=_resolve_for_gate,
            )
        )

    return await build_deep_agent(
        executor,
        tool_shells,
        workspace_id=workspace_id,
        db_factory=factory,
        extra_middleware=extra,
        system_prompt="spike executor",
        checkpointer=saver,
    )


async def _prove_readonly_bypass(factory, workspace_id, run_id) -> bool:
    """A read capability must BYPASS the ledger entirely (no reserve). Prove it in
    isolation with a tripwire ledger whose reserve() raises if ever called."""

    class _TripwireLedger:
        async def reserve(self, **_kw):
            raise AssertionError("ledger.reserve called for a READ capability — not a bypass!")

    inner_calls: list[str] = []

    async def _inner(tool_name, tool_input, *, user_id, workspace_id):  # noqa: ARG001
        inner_calls.append(tool_name)
        return {"status": "read-ok"}

    ctx = IdempotencyContext(
        ledger=_TripwireLedger(),
        run_id=run_id,
        step_id="s_read",
        workspace_id=workspace_id,
        db_factory=factory,
    )
    idem_fn = make_idempotent_execute_tool_fn(
        _inner, ctx, resolve_capability=_fake_resolve_capability
    )
    try:
        result = await idem_fn(READ_TOOL, {"q": "x"}, user_id="u", workspace_id=workspace_id)
    except AssertionError as exc:
        print(f"  [readonly] BYPASS FAILED: {exc}")
        return False
    ok = result == {"status": "read-ok"} and inner_calls == [READ_TOOL]
    print(f"  [readonly] read bypassed ledger (inner called, reserve NOT called) = {ok}")
    return ok


async def run_probe() -> int:  # noqa: PLR0915 - single linear spike orchestration
    if not await _reachable():
        print("EXACTLY_ONCE=SKIPPED (postgres unreachable)")
        return 1

    engine = create_async_engine(SQLA_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    run_id = f"run_{suffix}"
    thread_id = make_thread_id(workspace_id)

    exactly_once = False
    replayed = False
    ws_bound = False
    rows_not_pickle = False
    readonly_bypass = False
    sq4_composes = False
    serde_name = "?"

    # Patch the model factory so the REAL build_deep_agent path is exercised end to
    # end (middleware install, fail-closed guard, create_deep_agent) but the react
    # loop is driven by the deterministic _M fake instead of a live ChatAnthropic.
    original_build_chat_model = agent_builder.build_chat_model
    agent_builder.build_chat_model = lambda _agent: _M()

    saver = None
    pool = None
    try:
        # --- Seed FK chain + tool defs + effect table ----------------------
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"spike-{suffix}@example.com", display_name="spike"))
            db.add(Workspace(workspace_id=workspace_id, name="spike-ws", owner_user_id=user_id))
            await db.flush()
            db.add(
                ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name=WRITE_TOOL,
                    description="spike write",
                    capability="email.send",
                    requires_approval=True,  # => is_write_capability True
                    backend=ToolBackend.INTERNAL_MCP,
                    enabled=True,
                )
            )
            db.add(
                ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name=READ_TOOL,
                    description="spike read",
                    capability="email.read",
                    requires_approval=False,  # => read capability
                    backend=ToolBackend.INTERNAL_MCP,
                    enabled=True,
                )
            )
            await db.commit()

        async with factory() as db:
            await db.execute(text("CREATE TABLE IF NOT EXISTS spike_effects (id text primary key)"))
            await db.execute(text("DELETE FROM spike_effects"))
            await db.commit()

        executor = _make_executor()

        saver, pool = await build_async_postgres_saver(SQLA_URL)
        serde_name = type(saver.serde).__name__
        non_pickle_serde = isinstance(saver.serde, JsonPlusSerializer)
        print(f"SERDE={serde_name} NON_PICKLE_SERDE={non_pickle_serde}")

        cfg = {"configurable": {"thread_id": thread_id}}

        # --- Pass 1: real deep agent, expect the mid-tool-call crash ----------
        print("[pass 1] build_deep_agent + AsyncPostgresSaver — ainvoke(durability='sync')")
        agent1 = await _build_agent(
            executor, factory, workspace_id, user_id, run_id, saver
        )
        try:
            await agent1.ainvoke(
                {"messages": [{"role": "user", "content": "send it"}]},
                cfg,
                durability="sync",
            )
            print("[pass 1] UNEXPECTED: no crash raised")
        except RuntimeError as exc:
            print(f"[pass 1] caught expected crash: {exc}")

        dispatch_after_pass1 = len(DISPATCH_CALLS)

        # --- Pass 2: FRESH agent (simulated restart), resume same thread_id ----
        print("[pass 2] FRESH build_deep_agent, resume same thread_id — ainvoke(None, cfg)")
        agent2 = await _build_agent(
            executor, factory, workspace_id, user_id, run_id, saver
        )
        result = await agent2.ainvoke(None, cfg, durability="sync")
        final_msgs = result.get("messages", []) if isinstance(result, dict) else []
        print(f"[pass 2] resumed cleanly; final message count={len(final_msgs)}")

        # --- Assert exactly-once via DB-observed effect count -----------------
        async with factory() as db:
            count = (await db.execute(text("SELECT count(*) FROM spike_effects"))).scalar_one()
        exactly_once = count == 1
        replayed = len(DISPATCH_CALLS) >= 2
        print(
            f"[assert] spike_effects={count} dispatch_after_pass1={dispatch_after_pass1} "
            f"total_dispatch={len(DISPATCH_CALLS)} effects_fired={len(EFFECTS_FIRED)}"
        )

        # --- Ledger state check: the write row should be 'completed' ----------
        async with factory() as db:
            statuses = (
                await db.execute(
                    text(
                        "SELECT status FROM idempotency_ledger WHERE workspace_id = :ws"
                    ),
                    {"ws": workspace_id},
                )
            ).scalars().all()
        print(f"[assert] idempotency_ledger rows for ws = {list(statuses)}")

        # --- ws-bound thread id ----------------------------------------------
        ws_bound = workspace_of_thread_id(thread_id) == workspace_id
        print(f"[assert] workspace_of_thread_id({thread_id!r}) == {workspace_id!r} -> {ws_bound}")

        # --- checkpoint blobs are NOT pickle ---------------------------------
        rows_not_pickle = await _checkpoints_not_pickle(thread_id)

        # --- read-only bypasses the ledger -----------------------------------
        readonly_bypass = await _prove_readonly_bypass(factory, workspace_id, run_id)

        # --- SQ4: gated chain (authorization_source=AUTONOMOUS) composes -------
        try:
            gated_agent = await _build_agent(
                executor, factory, workspace_id, user_id, run_id, saver, gated=True
            )
            sq4_composes = gated_agent is not None
            print(
                "[SQ4] gated chain (capability_scope + ledger dispatcher + "
                f"trust_gate[AUTONOMOUS]) compiled with checkpointer -> {sq4_composes}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[SQ4] COMPILE FAILED: {exc!r}")
            sq4_composes = False

    finally:
        agent_builder.build_chat_model = original_build_chat_model
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:  # noqa: BLE001
                print(f"[cleanup] pool close failed: {exc!r}")
        try:
            async with factory() as db:
                await db.execute(text("DROP TABLE IF EXISTS spike_effects"))
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[cleanup] spike_effects drop failed: {exc!r}")
        try:
            async with factory() as db:
                # FK cascade (ondelete=CASCADE) removes tool_definitions + idempotency_ledger.
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[cleanup] FK teardown failed: {exc!r}")
        await _delete_checkpoints(thread_id)
        await engine.dispose()

    print("=" * 64)
    print("CRASH_MODEL=1 (kill after effect+record, before checkpoint; clean resume)")
    print(f"EXACTLY_ONCE={exactly_once}")
    print(f"REPLAYED_ON_RESUME={replayed}")
    print(f"WS_BOUND_THREAD_ID={ws_bound}")
    print(f"CHECKPOINT_ROWS_NOT_PICKLE={rows_not_pickle}")
    print(f"READONLY_BYPASSES_LEDGER={readonly_bypass}")
    print(f"SQ4_AUTONOMOUS_COMPOSES_WITH_CHECKPOINTER={sq4_composes}")
    print(f"SERDE={serde_name}")
    print("=" * 64)

    ok = (
        exactly_once
        and replayed
        and ws_bound
        and rows_not_pickle
        and readonly_bypass
        and sq4_composes
    )
    print(f"RESULT={'CONFIRMED' if ok else 'DISPROVEN'}")
    return 0 if ok else 2


async def _checkpoints_not_pickle(thread_id: str) -> bool:
    """Read the raw checkpoint blobs psycopg3 persisted and prove none is a pickle
    stream (a pickle payload starts with the PROTO opcode b'\\x80')."""
    conn = await asyncpg.connect(dsn=PSYCOPG_URL)
    try:
        rows = await conn.fetch(
            "SELECT type, blob FROM checkpoint_blobs WHERE thread_id = $1", thread_id
        )
        if not rows:
            print("[checkpoints] no checkpoint_blobs rows found (nothing to verify)")
            return False
        types = {r["type"] for r in rows}
        pickle_proto = pickle.PROTO  # b'\x80'
        any_pickle = any(r["blob"] and bytes(r["blob"][:1]) == pickle_proto for r in rows)
        print(f"[checkpoints] {len(rows)} blob rows, types={types}, any_pickle={any_pickle}")
        return not any_pickle
    finally:
        await conn.close()


async def _delete_checkpoints(thread_id: str) -> None:
    conn = await asyncpg.connect(dsn=PSYCOPG_URL)
    try:
        for tbl in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE thread_id = $1", thread_id)
            except Exception:  # noqa: BLE001 - table may not exist
                pass
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_probe()))
