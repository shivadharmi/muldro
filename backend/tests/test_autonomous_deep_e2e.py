"""OFFLINE end-to-end test of the autonomous deep durable run (Step 10C P7).

This is the capstone that exercises P1–P6 TOGETHER through
``graph_executor.execute_run`` / ``resume_run`` on the deep autonomous substrate
(deep is the only runtime — Step 11 Phase 4). Real Postgres + real Redis + a real
``AsyncPostgresSaver`` durable checkpointer; the ONLY fake is the react model
(patched ``build_chat_model``) and the leaf tool executor (records external
effects). The whole gated middleware chain (capability_scope → governor_audit →
unavailable_server → trust_gate[AUTONOMOUS] → write_lock → jarvis_tool_dispatcher),
the idempotency ledger, the DAG, the single TrustEngine step gate, the
runtime_events log, and the durable saver are all REAL. ``_forced_deep_gate`` is
retained as an honest no-op context manager (diff-stability) now that no gate exists.

Getting past the step gate
--------------------------
The write step must reach ``run_step_via_deep_agent`` without the run pausing, so
the DAG's single TrustEngine gate must AUTO-EXECUTE it. We patch
``trust_gate.get_or_assess_risk`` to a deterministic low/reversible assessment and
set ``executor._trust_engine`` to a stub whose ``evaluate`` returns
``auto_execute_silent`` (option (b) in the P7 spec). The deep trust_gate itself is
short-circuited for the step's already-step-gated capability via
``pre_approved_capabilities`` (SQ2 Branch C), so the write is never double-prompted.

Guarded (skip when Postgres/Redis unreachable), NullPool, seeded User→Workspace FK
chain, ULID-suffixed ids, teardown cascades every touched table + the durable
checkpoint rows. Reuses the ``_FakeModel`` + ``build_chat_model`` monkeypatch and
the real-DB harness shapes from ``tests/deep_runtime/test_autonomous_checkpointer.py``,
``tests/test_autonomous_lease.py``, ``tests/test_run_reconcile.py`` and
``spikes/deep_autonomous/probe_per_step_durable.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import redis.asyncio as redis_async
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.contracts import PolicyDecision
from src.deep_runtime import checkpoint_reaper
from src.deep_runtime.authorization import AuthorizationSource
from src.deep_runtime.checkpointer import build_async_postgres_saver
from src.deep_runtime.thread_identity import make_thread_id
from src.models.idempotency_ledger import IdempotencyLedgerEntry
from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskCheckpoint, TaskRun, TaskStep
from src.models.tool_definitions import ToolBackend, ToolDefinition
from src.models.users import User, Workspace
from src.orchestrator.agent_invoker import AgentInvoker
from src.services.autonomous_lease import run_lease_key
from src.services.idempotency import (
    IdempotencyContext,
    IdempotencyLedger,
    make_idempotent_execute_tool_fn,
)
from src.services.runtime_projection import RuntimeProjectionService
from tests.conftest import make_mock_settings

INVOKER_MODULE = "src.orchestrator.agent_invoker"
BUILD_CHAT_MODEL = "src.deep_runtime.agent_builder.build_chat_model"
GET_OR_ASSESS_RISK = "src.services.trust_gate.get_or_assess_risk"

WRITE_TOOL = "send_email"
READ_TOOL = "read_email"
# email.send identity = (to, cc, bcc, subject) — stable across resume regardless of body.
WRITE_ARGS = {"to": "founder@example.com", "subject": "quarterly update"}
READ_ARGS = {"q": "investor"}


# ─────────────────────────── reachability guards ────────────────────────────


def _psycopg_dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "", 1)


def _sqla_dsn() -> str:
    return get_settings().database_url


def _db_reachable() -> bool:
    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=_psycopg_dsn())
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover
        return False


def _redis_reachable() -> bool:
    try:
        import redis

        redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception:  # pragma: no cover
        return False


_DB_OK = _db_reachable()
_REDIS_OK = _redis_reachable()

pytestmark = pytest.mark.skipif(not (_DB_OK and _REDIS_OK), reason="requires live Postgres + Redis")


# ─────────────────────────── the step-aware fake model ───────────────────────


class _FakeModel(BaseChatModel):
    """Step-aware deterministic no-API model. On ``bind_tools`` it records the bound
    tool names; on turn 1 it emits a tool_call for the highest-PREFERENCE bound tool
    that has known args (so a read step calls the read tool and a write step — whose
    scope also includes the family read — calls the WRITE tool); after it sees a
    ToolMessage it emits a final ``done`` text chunk. Copied in shape from
    ``test_autonomous_checkpointer.py`` and made tool-set aware."""

    def __init__(self, args_by_tool: dict[str, dict], prefer: list[str], **kw: Any) -> None:
        super().__init__(**kw)
        object.__setattr__(self, "_args_by_tool", args_by_tool)
        object.__setattr__(self, "_prefer", prefer)
        object.__setattr__(self, "_bound", [])

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        names: list[str] = []
        for t in tools:
            n = getattr(t, "name", None)
            if n is None and isinstance(t, dict):
                n = t.get("name")
            if n:
                names.append(n)
        object.__setattr__(self, "_bound", names)
        return self

    def _pick(self) -> str | None:
        for name in self._prefer:
            if name in self._bound and name in self._args_by_tool:
                return name
        for name in self._bound:
            if name in self._args_by_tool:
                return name
        return None

    def _script(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return [AIMessageChunk(content=[{"type": "text", "text": "done", "index": 0}])]
        tool = self._pick()
        if tool is None:
            return [AIMessageChunk(content=[{"type": "text", "text": "done", "index": 0}])]
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name=tool,
                        args=json.dumps(self._args_by_tool[tool]),
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


def _fake_model_factory():
    """A ``build_chat_model`` replacement: FRESH ``_FakeModel`` per build (per DAG step /
    per deep agent) so each step captures its own bound tool set. Writes preferred over
    reads so the write step (scope: send_email + family read) fires the WRITE."""
    return lambda _agent: _FakeModel(
        {READ_TOOL: READ_ARGS, WRITE_TOOL: WRITE_ARGS}, prefer=[WRITE_TOOL, READ_TOOL]
    )


# ─────────────────────────── real-DB / real-Redis harness ────────────────────


class _RecordingToolExecutor:
    """A ToolExecutor-shaped fake: ``execute_tool`` records each external effect and
    returns a 'sent' envelope. Positional-or-keyword ``user_id``/``workspace_id`` so it
    satisfies BOTH the deep dispatcher's positional call AND agent_loop's keyword call."""

    def __init__(self, effects: list[str]) -> None:
        self._effects = effects

    async def execute_tool(self, name, args, user_id, workspace_id):  # noqa: ARG002
        self._effects.append(name)
        return {"status": "sent", "tool": name}


@asynccontextmanager
async def _db_env():
    """Real-DB env: NullPool engine + User→Workspace seed. Yields
    ``(factory, workspace_id, user_id, threads)``; ``threads`` is a set the test fills with
    durable checkpoint thread_ids. Teardown deletes those checkpoint rows and every table
    the e2e touches (FK-safe order), then the Workspace + User."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    threads: set[str] = set()
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"p7-{suffix}@example.com", display_name="p7"))
            db.add(Workspace(workspace_id=workspace_id, name="p7-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id, threads
    finally:
        for tid in threads:
            await _delete_checkpoints(tid)
        await _teardown_workspace(factory, workspace_id, user_id)
        await engine.dispose()


async def _teardown_workspace(factory, workspace_id: str, user_id: str) -> None:
    """Delete every row this e2e can create for the workspace, in FK-safe order, each in its
    own try (a table may not carry the row). Many FK to workspaces ON DELETE CASCADE, so the
    explicit deletes are belt-and-suspenders + cover any table without a cascade."""
    from src.models.approvals import Approval
    from src.models.runtime_event import RuntimeEvent
    from src.models.trust_state import TrustState
    from src.models.ui_state import UISurface

    ws_scoped = [
        RuntimeEvent,
        TaskCheckpoint,
        TaskStep,
        TaskRun,
        PlanTask,
        Plan,
        ToolDefinition,
        IdempotencyLedgerEntry,
        Approval,
        TrustState,
        UISurface,
    ]
    for model in ws_scoped:
        try:
            async with factory() as db:
                await db.execute(delete(model).where(model.workspace_id == workspace_id))
                await db.commit()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
    try:
        async with factory() as db:
            await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
            await db.execute(delete(User).where(User.user_id == user_id))
            await db.commit()
    except Exception:  # pragma: no cover
        pass


async def _seed_tools(factory, workspace_id: str) -> None:
    """Seed the write tool (email.send, requires_approval → is_write) + the read tool
    (email.read, no approval → read). Same shape as the checkpointer harness."""
    for name, capability, requires_approval in (
        (WRITE_TOOL, "email.send", True),
        (READ_TOOL, "email.read", False),
    ):
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
                    input_schema={
                        "type": "object",
                        "properties": {"to": {"type": "string"}, "q": {"type": "string"}},
                    },
                )
            )
            await db.commit()


async def _seed_read_write_plan(factory, workspace_id: str, user_id: str) -> str:
    """Seed a Plan with two ordered PlanTasks: a read (email.read) then a write (email.send)
    that depends on the read. ``create_run`` folds these into a read→write step DAG."""
    plan_id = f"plan_{ULID()}"
    read_task = f"t_read_{ULID()}"
    write_task = f"t_write_{ULID()}"
    async with factory() as db:
        db.add(
            Plan(
                plan_id=plan_id,
                user_id=user_id,
                workspace_id=workspace_id,
                trigger_type="test",
                goal="reply to the investor",
                decision="execute_plan",
            )
        )
        db.add(
            PlanTask(
                task_id=read_task,
                plan_id=plan_id,
                workspace_id=workspace_id,
                task_type="email.read",
                input_data={"capability": "email.read", "goal": "read the investor email"},
            )
        )
        db.add(
            PlanTask(
                task_id=write_task,
                plan_id=plan_id,
                workspace_id=workspace_id,
                task_type="email.send",
                input_data={"capability": "email.send", "goal": "send the reply"},
                depends_on=[read_task],
            )
        )
        await db.commit()
    return plan_id


def _make_invoker(factory, *, checkpointer, effects: list[str]) -> AgentInvoker:
    """AgentInvoker wired to the real factory + a real (injected) checkpointer + a recording
    tool executor. services=None → the middleware's redis lookups resolve None (write-lock
    fail-open, risk redis None) — matching the checkpointer harness."""
    from src.orchestrator.agents import AGENTS

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", resolved_model="claude-test"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: factory,
        tool_executor=_RecordingToolExecutor(effects),
        context=MagicMock(),
        agents={"executor": AGENTS["executor"]},
        checkpointer_provider=lambda: checkpointer,
    )


def _auto_execute_engine() -> MagicMock:
    """A TrustEngine stub whose ``evaluate`` always returns ``auto_execute_silent`` — so the
    DAG's single step gate auto-executes the write without pausing (P7 spec option (b))."""
    engine = MagicMock()
    engine.evaluate = AsyncMock(
        return_value=PolicyDecision(decision="auto_execute_silent", justification="e2e-auto")
    )
    return engine


def _low_reversible_risk():
    from src.services.risk_assessor import RiskAssessment

    return RiskAssessment(
        risk_level="low",
        reasoning="e2e deterministic low/reversible",
        reversible=True,
        blast_radius="self",
    )


def _build_graph_executor(*, db, factory, redis, deep_step_runner, execute_tool_fn):
    """A GraphExecutor built DIRECTLY (not via the factory, which would pull real
    EventBus/Notifier/Verifier/etc). verifier/notifier/context_builder/memory omitted so the
    completion path stays clean (run finalizes ``completed``); trust_engine + audit are
    overridden after construction. redis is real (lease + effective_runtime gate)."""
    from src.services.graph_executor import GraphExecutor

    with patch("src.services.graph_executor.get_anthropic_client", return_value=MagicMock()):
        gx = GraphExecutor(
            make_mock_settings(runtime="legacy", resolved_model="claude-test"),
            db,
            db_factory=factory,
            execute_tool_fn=execute_tool_fn,
            budget=MagicMock(),
            circuit_breaker=MagicMock(),
            redis=redis,
            deep_step_runner=deep_step_runner,
        )
    gx._trust_engine = _auto_execute_engine()
    # Audit logging is an orthogonal side-record; stub it so the e2e never depends on the
    # audit_log table shape (the P1–P6 seams under test are unaffected).
    gx._audit.log = AsyncMock()
    return gx


# ── durable-checkpoint + ledger + Redis-gate helpers ─────────────────────────


async def _checkpoint_rows(thread_id: str) -> int:
    conn = await asyncpg.connect(dsn=_psycopg_dsn())
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM checkpoints WHERE thread_id = $1", thread_id
        )
    finally:
        await conn.close()


async def _delete_checkpoints(thread_id: str) -> None:
    conn = await asyncpg.connect(dsn=_psycopg_dsn())
    try:
        for tbl in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE thread_id = $1", thread_id)
            except Exception:  # noqa: BLE001 - table may not exist
                pass
    finally:
        await conn.close()


async def _ledger_rows(factory, workspace_id: str) -> list[IdempotencyLedgerEntry]:
    async with factory() as db:
        return list(
            (
                await db.execute(
                    select(IdempotencyLedgerEntry).where(
                        IdempotencyLedgerEntry.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )


async def _reload_run_and_steps(factory, run_id: str) -> tuple[TaskRun, list[TaskStep]]:
    async with factory() as db:
        run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
        steps = list(
            (await db.execute(select(TaskStep).where(TaskStep.run_id == run_id))).scalars().all()
        )
        return run, steps


@asynccontextmanager
async def _forced_deep_gate(redis):
    """No-op: deep is the only runtime (Step 11 Phase 4). Kept as a context manager so the
    two e2e tests that used it read unchanged."""
    yield


# ═══════════════════════ TEST 1 — happy-path e2e (assertions 1–5) ═══════════════════════


async def test_autonomous_deep_e2e_happy_path():
    """FORCED-ON e2e through ``execute_run``: a background read→write plan runs entirely on
    the DEEP durable substrate. Asserts (1) the write ran via the deep path (not legacy),
    (2) the external write fired EXACTLY ONCE with a completed idempotency_ledger row,
    (3) the run reached a terminal status, (4) the runtime_events log rebuilds to the live
    completed-step count, and (5) the write step's durable checkpoint was written then
    reaped on completion."""
    async with _db_env() as (factory, ws, uid, threads):
        await _seed_tools(factory, ws)
        plan_id = await _seed_read_write_plan(factory, ws, uid)

        effects: list[str] = []
        r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
        saver, pool = await build_async_postgres_saver(_sqla_dsn())
        invoker = _make_invoker(factory, checkpointer=saver, effects=effects)

        deep_calls: list[frozenset[str]] = []
        real_deep = invoker.run_autonomous_deep_step

        async def _deep_spy(**kw):
            deep_calls.append(kw.get("pre_approved_capabilities"))
            return await real_deep(**kw)

        captured_threads: list[str] = []

        def _thread_spy(workspace_id: str) -> str:
            tid = make_thread_id(workspace_id)
            captured_threads.append(tid)
            threads.add(tid)
            return tid

        # Reap spy: record (rows_before, rows_after) so we PROVE both durability (a real
        # checkpoint was written) AND the reap (it was deleted on completion).
        reap_records: dict[str, tuple[int, int]] = {}

        async def _reap_spy(s, tid):
            before = await _checkpoint_rows(tid)
            ok = await checkpoint_reaper.reap_thread(s, tid)
            after = await _checkpoint_rows(tid)
            reap_records[tid] = (before, after)
            return ok

        run_id: str | None = None
        try:
            async with factory() as db:
                gx = _build_graph_executor(
                    db=db,
                    factory=factory,
                    redis=r,
                    deep_step_runner=_deep_spy,
                    execute_tool_fn=_RecordingToolExecutor(effects).execute_tool,
                )

                run = await gx.create_run(plan_id, uid, ws, source="background")
                run_id = run.run_id
                await db.flush()

                async with _forced_deep_gate(r):
                    with (
                        patch(BUILD_CHAT_MODEL, _fake_model_factory()),
                        patch(f"{INVOKER_MODULE}.make_thread_id", _thread_spy),
                        patch(f"{INVOKER_MODULE}.reap_thread", _reap_spy),
                        patch(GET_OR_ASSESS_RISK, AsyncMock(return_value=_low_reversible_risk())),
                    ):
                        result_run = await gx.execute_run(run_id)

            # ── Assertion 1: the write ran via the DEEP step executor.
            assert frozenset({"email.send"}) in deep_calls, deep_calls
            assert frozenset({"email.read"}) in deep_calls, deep_calls

            # ── Assertion 2: the external write fired EXACTLY ONCE + a completed ledger row.
            assert effects.count(WRITE_TOOL) == 1, effects
            assert effects.count(READ_TOOL) == 1, effects  # the read fired (ledger-bypassed)
            ledger = await _ledger_rows(factory, ws)
            assert len(ledger) == 1, [(e.capability, e.status) for e in ledger]
            assert ledger[0].capability == "email.send"
            assert ledger[0].status == "completed"

            # ── Assertion 3: the run reached a TERMINAL status.
            assert result_run.status in ("completed", "partially_completed"), result_run.status
            reloaded, steps = await _reload_run_and_steps(factory, run_id)
            assert reloaded.status in ("completed", "partially_completed")

            # ── Assertion 4: the runtime_events log is a faithful system-of-record — the
            # projection's completed-step count equals the live completed-step count.
            from src.services.execution_state import TERMINAL_SUCCESS

            live_completed = sum(1 for s in steps if s.status in TERMINAL_SUCCESS)
            assert live_completed == 2, [s.status for s in steps]
            async with factory() as db:
                proj = await RuntimeProjectionService(db, ws).rebuild_run_projection(run_id)
            assert proj["completed_steps"] == live_completed, proj

            # ── Assertion 5: the write step's durable checkpoint was WRITTEN then REAPED.
            # captured_threads == [read_thread, write_thread] (one make_thread_id per step).
            assert len(captured_threads) == 2, captured_threads
            write_thread = captured_threads[-1]
            before, after = reap_records[write_thread]
            assert before >= 1, f"no durable checkpoint was written for {write_thread}"
            assert after == 0, f"checkpoint not reaped for {write_thread}"
            # No durable checkpoint rows survive for ANY per-step thread.
            for tid in captured_threads:
                assert await _checkpoint_rows(tid) == 0
        finally:
            await pool.close()
            await r.aclose()


# ═══════════ TEST 2 — kill + resume dedup (assertion 6, also the NC-4 witness) ═══════════


async def test_autonomous_deep_e2e_kill_resume_dedup():
    """Assertion 6 (+ the NC-4 witness). A write is fired + ledger-recorded, then the react
    loop is KILLED mid-tool-call (RuntimeError after the effect + record, like
    probe_per_step_durable). The run is left resumable with a checkpoint/DB mismatch, then
    ``resume_run`` drives the DEEP autonomous path end-to-end through the DAG: the P3 lease is
    taken, P4 reconcile-from-event-log runs, the write step is re-driven, and the P1 ledger
    dedups it so the external effect stays EXACTLY ONCE and the run finishes terminal."""
    from src.orchestrator.agents import AGENTS

    async with _db_env() as (factory, ws, uid, threads):
        await _seed_tools(factory, ws)

        run_id = f"run_{ULID()}"
        write_step_id = f"step_{ULID()}"
        # Seed a single-write run left in the durable "mid-crash" shape: run paused (resumable),
        # write step still "running", and a checkpoint claiming the write completed while the DB
        # row disagrees → the P4 reconcile branch fires on resume.
        async with factory() as db:
            db.add(
                TaskRun(
                    run_id=run_id,
                    user_id=uid,
                    workspace_id=ws,
                    source="background",
                    status="paused",
                    checkpoint={"completed_steps": {write_step_id: {}}, "surface_id": run_id},
                )
            )
            db.add(
                TaskStep(
                    step_id=write_step_id,
                    run_id=run_id,
                    workspace_id=ws,
                    task_id="t_write",
                    status="running",
                    input_data={"capability": "email.send", "goal": "send the reply"},
                )
            )
            await db.commit()

        effects: list[str] = []
        crashed: list[int] = []
        r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
        saver, pool = await build_async_postgres_saver(_sqla_dsn())
        invoker = _make_invoker(factory, checkpointer=saver, effects=effects)

        def _crash_ledger_execute_tool():
            """A ledger-wrapped execute_tool that fires the effect + records the ledger, then
            raises ONCE on the write — a process kill AFTER the record but BEFORE the tool-node
            checkpoint. Built fresh (fresh ledger + ordinal) so it models a restart. Same
            (run_id, step_id, ws) + args as the resume, so the ledger identity keys match."""

            async def _effect(name, args, *, user_id, workspace_id):  # noqa: ARG001
                effects.append(name)
                return {"status": "sent", "tool": name}

            idem_fn = make_idempotent_execute_tool_fn(
                _effect,
                IdempotencyContext(
                    ledger=IdempotencyLedger(factory),
                    run_id=run_id,
                    step_id=write_step_id,
                    workspace_id=ws,
                    db_factory=factory,
                ),
            )

            async def _adapter(name, args, user_id, workspace_id):  # dispatcher positional contract
                result = await idem_fn(name, args, user_id=user_id, workspace_id=workspace_id)
                if name == WRITE_TOOL and not crashed:
                    crashed.append(1)
                    raise RuntimeError("simulated process kill after effect+record")
                return result

            return _adapter

        try:
            # ── PHASE 1: the crash (step-level, mirroring the proven Test-3 harness). Fires the
            # effect + records the ledger completed, then kills the react loop mid-tool-call.
            crash_thread = make_thread_id(ws)
            threads.add(crash_thread)
            with patch(BUILD_CHAT_MODEL, _fake_model_factory()):
                agent = await invoker._build_deep_agent_for(
                    AGENTS["executor"],
                    [{"name": WRITE_TOOL, "description": "send", "input_schema": {}}],
                    user_id=uid,
                    workspace_id=ws,
                    thread_id=crash_thread,
                    authorization_source=AuthorizationSource.AUTONOMOUS,
                    system_prompt="exec",
                    execute_tool=_crash_ledger_execute_tool(),
                    pre_approved_capabilities=frozenset({"email.send"}),
                )
                with pytest.raises(RuntimeError, match="simulated process kill"):
                    await agent.ainvoke(
                        {"messages": [{"role": "user", "content": "send it"}]},
                        {"configurable": {"thread_id": crash_thread}},
                        durability="sync",
                    )

            assert effects == [WRITE_TOOL]  # the effect fired exactly once in the crash
            assert crashed == [1]
            pre_ledger = await _ledger_rows(factory, ws)
            assert len(pre_ledger) == 1 and pre_ledger[0].status == "completed"

            # ── PHASE 2: resume end-to-end through the DAG. Spy on the reconcile so we prove the
            # P4 reconcile-from-event-log ran on the deep resume.
            reconcile_spy_wrapper = {}
            from src.services import run_reconcile as _rr

            real_reconcile = _rr.reconcile_run_from_events

            async def _reconcile_spy(db, run):
                reconcile_spy_wrapper["called"] = True
                return await real_reconcile(db, run)

            async with factory() as db:
                gx = _build_graph_executor(
                    db=db,
                    factory=factory,
                    redis=r,
                    deep_step_runner=invoker.run_autonomous_deep_step,
                    execute_tool_fn=_RecordingToolExecutor(effects).execute_tool,
                )
                async with _forced_deep_gate(r):
                    with (
                        patch(BUILD_CHAT_MODEL, _fake_model_factory()),
                        patch(f"{INVOKER_MODULE}.reap_thread", AsyncMock(return_value=True)),
                        patch(GET_OR_ASSESS_RISK, AsyncMock(return_value=_low_reversible_risk())),
                        # _resume_run_body imports reconcile_run_from_events locally, so patch
                        # it at its source module (mirrors tests/test_run_reconcile.py).
                        patch(
                            "src.services.run_reconcile.reconcile_run_from_events",
                            _reconcile_spy,
                        ),
                    ):
                        result_run = await gx.resume_run(run_id)

            # The reconcile-from-event-log ran on the deep resume (P4).
            assert reconcile_spy_wrapper.get("called") is True
            # THE DEDUP: the external write remains EXACTLY ONCE across crash + resume (P1
            # ledger). Under NC-4 (ledger dropped) the resume double-fires → effects == 2 → RED.
            assert effects.count(WRITE_TOOL) == 1, effects
            # The run finished terminal.
            assert result_run.status in ("completed", "partially_completed"), result_run.status
            ledger = await _ledger_rows(factory, ws)
            assert len(ledger) == 1 and ledger[0].status == "completed"
        finally:
            await pool.close()
            await r.aclose()


# ═══════════ NEGATIVE CONTROL 1 — gate legacy ⇒ byte-identical legacy routing ═══════════


# ═══════════ NEGATIVE CONTROL 2 — A6 ws-bound thread guard refuses cross-ws ═══════════


async def test_nc2_cross_ws_thread_refused():
    """NC-2: the A6 ws-bound-thread guard in ``run_autonomous_deep_step``. With ``make_thread_id``
    patched to mint a thread embedding a DIFFERENT workspace, the step is refused with the
    ws-mismatch error and the deep agent is NEVER built. Mutating the guard
    (``if workspace_of_thread_id(thread_id) != (workspace_id or ""):`` → ``if False:``) lets
    the cross-ws thread through → this assertion goes RED. Restore to GREEN."""
    async with _db_env() as (factory, ws, uid, threads):  # noqa: F841
        await _seed_tools(factory, ws)
        effects: list[str] = []
        invoker = _make_invoker(factory, checkpointer=None, effects=effects)
        invoker._build_deep_agent_for = AsyncMock(return_value=object())

        def _cross_ws_make_thread_id(_workspace_id: str) -> str:
            return make_thread_id("ws_someone_else")

        async def _done_stream(*a, **k):
            yield {"event": "agent_done", "agent": "executor", "text": "done", "tools_called": []}

        with (
            patch(f"{INVOKER_MODULE}.make_thread_id", _cross_ws_make_thread_id),
            patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _done_stream),
        ):
            from src.orchestrator.agents import AGENTS

            out = await invoker.run_autonomous_deep_step(
                executor=AGENTS["executor"],
                tools=[{"name": WRITE_TOOL, "description": "send", "input_schema": {}}],
                message="send it",
                context_block="",
                user_id=uid,
                workspace_id=ws,  # != the cross-ws thread the patched minter returns
                run_id="run_nc2",
                step_id="s1",
                pre_approved_capabilities=frozenset({"email.send"}),
            )

        # GREEN (guard intact): refused before any deep build; no effect fires.
        assert out["status"] == "error"
        assert out["errors"] == ["workspace thread mismatch"]
        invoker._build_deep_agent_for.assert_not_awaited()
        assert effects == []


# ═══════════ NEGATIVE CONTROL 3 — single-flight lease NX (double-drive) ═══════════


async def test_nc3_lease_single_flight_double_drive_guard():
    """NC-3: the Redis lease's ``SET NX`` makes exactly ONE of two concurrent deep-gated
    ``execute_run`` calls drive the run body; the other backs off. Dropping the ``nx=True`` in
    ``acquire_run_lease`` lets BOTH acquire → the body runs twice → this assertion goes RED.
    Uses the proven ``test_autonomous_lease`` integration shape (stubbed body)."""
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    run_id = f"run_{ULID()}"
    key = run_lease_key(run_id)
    try:
        with patch("src.services.graph_executor.get_anthropic_client", return_value=MagicMock()):
            from src.services.graph_executor import GraphExecutor

            gx = GraphExecutor(make_mock_settings(runtime="legacy"), AsyncMock(), redis=r)

        run = MagicMock()
        run.run_id = run_id
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        gx._db.execute = AsyncMock(return_value=mock_result)

        async def _slow_body(rid, **kwargs):  # noqa: ARG001
            await asyncio.sleep(0.3)  # hold the lease across an await so the 2nd caller observes it
            return run

        body_spy = AsyncMock(side_effect=_slow_body)
        gx._execute_run_body = body_spy

        results = await asyncio.gather(gx.execute_run(run_id), gx.execute_run(run_id))

        # GREEN (NX intact): exactly one worker drove the body; both return a valid run.
        assert body_spy.call_count == 1
        assert all(res is run for res in results)
    finally:
        await r.delete(key)
        await r.aclose()


# ═══════════ NEGATIVE CONTROL 5 — reconcile never regresses a terminal-success step ═══════════


async def test_nc5_reconcile_no_regress_terminal_step():
    """NC-5: ``reconcile_run_from_events`` is UP-ONLY — it upgrades a behind step but NEVER
    downgrades a step already in ``TERMINAL_SUCCESS``. A ``completed_unverified`` step that the
    log ALSO records completed must be left alone. Removing the ``step.status not in
    TERMINAL_SUCCESS`` guard in ``run_reconcile`` regresses it (completed_unverified→completed)
    → this assertion goes RED. Mirrors ``test_run_reconcile`` no-regress with teeth."""
    from datetime import datetime, timezone

    from src.models.runtime_event import RuntimeEvent
    from src.services.run_reconcile import reconcile_run_from_events

    async with _db_env() as (factory, ws, uid, threads):  # noqa: F841
        s1 = f"step_{ULID()}"
        run_id = f"run_{ULID()}"
        tied = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
        async with factory() as db:
            db.add(
                TaskRun(
                    run_id=run_id, user_id=uid, workspace_id=ws, source="plan", status="running"
                )
            )
            # A terminal-SUCCESS step that is ALSO upgradeable (completed_unverified→completed),
            # so the guard has an OBSERVABLE regression to prevent.
            db.add(
                TaskStep(
                    step_id=s1,
                    run_id=run_id,
                    workspace_id=ws,
                    task_id="t",
                    status="completed_unverified",
                )
            )
            for et in ("step_started", "step_completed"):
                db.add(
                    RuntimeEvent(
                        event_id=f"revt_{ULID()}",
                        workspace_id=ws,
                        run_id=run_id,
                        step_id=s1,
                        event_type=et,
                        payload={"step_id": s1, "status": "completed", "run_id": run_id},
                        occurred_at=tied,
                    )
                )
                await db.flush()
            await db.commit()

        async with factory() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one()
            summary = await reconcile_run_from_events(db, run)
            await db.commit()

        # GREEN (guard intact): the terminal-success step is neither upgraded nor regressed.
        assert summary["reconciled_steps"] == 0, summary
        _run, steps = await _reload_run_and_steps(factory, run_id)
        assert steps[0].status == "completed_unverified"
