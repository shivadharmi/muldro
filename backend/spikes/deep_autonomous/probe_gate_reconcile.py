"""Spike probe (Step 10C Phase 0.3 — SQ2 crux: gate reconciliation).

Once the autonomous step-executor becomes a deep agent with
``authorization_source=AUTONOMOUS`` (10C), TWO approval gates exist for a write step:

  * STEP-level gate (``dag_runner.execute_step`` :337-346): ``assess_step_risk`` →
    ``TrustEngine.evaluate`` → on ``approval_required`` pauses the run to
    ``awaiting_approval`` and persists ``Approval(run_id, step_id)``. DURABLE,
    scheduler-driven — the autonomous system relies on it. It STAYS.
  * Deep TOOL-CALL gate (``trust_gate.make_trust_gate_middleware``): for a gated
    source it resolves capability, assesses risk, persists ``Approval(workspace_id,
    thread_id, tool_call_id)`` and ``interrupt()``s. ``is_gated_source("autonomous")
    == True`` (authorization.py:23), so for an AUTONOMOUS WRITE it WILL interrupt.

⇒ For an autonomous write step BOTH gates fire = DOUBLE approval. This probe OBSERVES
the double-gate and proves the Branch-C resolution: a capability-set short-circuit at
the deep gate (NO thread_id change → no migration) that lets an already-approved step's
capability pass through, while STILL gating a DIFFERENT un-approved write capability.

What it proves (all against REAL Postgres for Approval persistence, MemorySaver for
the checkpoint — durability is proven in 0.1; this spike is about gating):

  Obs 1  DEEP_GATE_INTERRUPTS_FOR_AUTONOMOUS_WRITE — the REAL gate, AUTONOMOUS + a
         write (email.send), reaches ``interrupt()`` (graph pauses, tool NOT executed,
         exactly ONE Approval persisted for the thread).
  Obs 2  Branch-C capability-set short-circuit is feasible WITHOUT a thread_id change:
           2a PREAPPROVED_CAP_SHORT_CIRCUITS — email.send ∈ pre_approved → passes
              through (tool executes, NO interrupt, NO Approval row).
           2b UNAPPROVED_WRITE_STILL_GATES — payment.send ∉ pre_approved → STILL
              interrupts (Approval persisted, tool NOT executed) → the gate is NOT
              dead-wired; it still gates within-step capability expansion.
           thread_id stays ``make_thread_id(ws)`` (≤ 64 chars, ws recoverable).
  Obs 3  read-back seam shape (SQ3, static confirmation) — the deep step output dict
         ``{"status","result","tools_called","errors"}`` (+ ``auth_required``
         passthrough) is exactly what ``dag_runner._finalize_with_verification`` and its
         pre-finalize ``_detect_auth_required`` consume without KeyError.

Run:
    uv run python -m spikes.deep_autonomous.probe_gate_reconcile

Self-contained + re-runnable: seeds a UUID/ULID-suffixed User+Workspace FK chain and
two ToolDefinition rows (email.send write + payment.send write); tears everything down
(Approvals + TrustState + tool defs + FK chain) in a finally. Exploratory spike code —
module-level prints + broad orchestration — but it lints clean.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
from langchain.agents.middleware import wrap_tool_call
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

import src.deep_runtime.agent_builder as agent_builder
from src.config.settings import get_settings
from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.authorization import AuthorizationSource, is_gated_source
from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.deep_runtime.middleware.muldro_tool_dispatcher import make_muldro_tool_dispatcher
from src.deep_runtime.middleware.trust_gate import (
    _decide_and_maybe_persist,
    _find_existing_approval,
    _resolve_capability,
    make_trust_gate_middleware,
)
from src.deep_runtime.thread_identity import make_thread_id, workspace_of_thread_id
from src.deep_runtime.tool_bridge import build_tool_shells
from src.integrations.capabilities import is_read_only_capability
from src.models.approvals import Approval
from src.models.tool_definitions import ToolBackend, ToolDefinition
from src.models.trust_state import TrustState
from src.models.users import User, Workspace
from src.services.risk_assessor import RiskAssessment

# --- Connection strings -----------------------------------------------------
SETTINGS = get_settings()
SQLA_URL = SETTINGS.database_url  # postgresql+asyncpg://...
PSYCOPG_URL = SQLA_URL.replace("+asyncpg", "", 1)

WRITE_TOOL = "spike_send_email"  # capability email.send
PAY_TOOL = "spike_send_payment"  # capability payment.send
WRITE_CAP = "email.send"
PAY_CAP = "payment.send"

# Module-level record of tools that actually EXECUTED through the dispatcher (i.e. the
# gate let the call reach the inner handler). Empty after an interrupt/short-circuit-deny.
EXECUTED: list[str] = []


# --------------------------------------------------------------------------- #
# Fake deterministic chat model (no API), parametrized by which tool to call.
# turn 1 → tool_call; turn 2 (after a ToolMessage) → final "done" text.
# --------------------------------------------------------------------------- #
class _M(BaseChatModel):
    def __init__(self, tool_name: str, **kw: Any) -> None:
        super().__init__(**kw)
        # BaseChatModel is a pydantic model; stash the target on a private attr.
        object.__setattr__(self, "_tool_name", tool_name)

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
                        args=json.dumps({"to": "founder@example.com", "amount": 100}),
                        id="call_spike_gate",
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
    """Executor SubAgent whose capability_scope INCLUDES both write caps so
    build_deep_agent's fail-closed guard is satisfied and the capability_scope
    middleware allows BOTH spike tools."""
    from src.orchestrator.agents import SubAgent, ThinkingConfig

    return SubAgent(
        name="executor",
        prompt="spike executor",
        model_tier="haiku",
        capability_scope={WRITE_CAP, PAY_CAP},
        max_tokens=1024,
        temperature=0.0,
        thinking=ThinkingConfig(enabled=False),
    )


async def _gating_risk(capability, tool_input):  # noqa: ARG001
    """DB-free assess_risk that ALWAYS gates: high + irreversible external write.
    Matches the ``assess_risk`` contract the gate calls (``.risk_level/.reversible/
    .blast_radius/.reasoning``)."""
    return RiskAssessment(
        risk_level="high",
        reasoning="spike: gating write (high, irreversible)",
        reversible=False,
        blast_radius="external_single",
    )


def _build_dispatcher(user_id, workspace_id):
    """The INNER handler: records execution + returns a benign 'sent' dict. If the gate
    short-circuits (interrupt / deny), this is never reached and EXECUTED stays empty."""

    async def _execute(name, args, uid, wsid):  # positional (dispatcher contract)  # noqa: ARG001
        EXECUTED.append(name)
        print(f"  [dispatch] tool EXECUTED name={name}")
        return {"status": "sent", "tool": name}

    return make_muldro_tool_dispatcher(
        execute_tool=_execute, user_id=user_id, workspace_id=workspace_id
    )


# --------------------------------------------------------------------------- #
# Branch-C LOCAL variant of the deep trust_gate. It is the REAL gate body with ONE
# added pre-check — ``if capability in pre_approved_capabilities: pass through`` —
# proving the short-circuit needs only a capability-set param (captured at the seam
# from the step's already-approved capability), NO thread_id change. Everything else
# (resolve → read-only pass → replay find → risk → decide+persist → interrupt) reuses
# the REAL module helpers verbatim, so the delta from src is exactly this one line.
# --------------------------------------------------------------------------- #
def make_branch_c_trust_gate(
    *,
    authorization_source: str,
    workspace_id: str,
    user_id: str,
    thread_id: str,
    agent_name: str,
    db_factory,
    assess_risk,
    pre_approved_capabilities: set[str],
):
    async def resolve_capability(name: str) -> tuple[bool, str | None]:
        return await _resolve_capability(name, workspace_id, db_factory)

    @wrap_tool_call
    async def trust_gate(request, handler):
        name = request.tool_call["name"]
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)
        if not is_gated_source(authorization_source):
            return await handler(request)

        tool_call_id = request.tool_call["id"]
        args = request.tool_call.get("args") or {}

        lookup_ok, capability = await resolve_capability(name)
        if not lookup_ok:
            return ToolMessage(
                content=json.dumps(
                    {"error": "capability lookup failed — blocked (fail-closed)", "blocked": True}
                ),
                tool_call_id=tool_call_id,
                name=name,
                status="error",
            )
        if not capability or is_read_only_capability(capability):
            return await handler(request)

        # ── BRANCH-C DELTA (the ONLY change vs the real gate) ─────────────────
        # This step's capability was already approved by the STEP-level TrustEngine
        # gate (dag_runner). Correlate by CAPABILITY captured at the seam — NOT by a
        # thread_id lookup (thread_id has only ~6 chars headroom in String(64), so it
        # cannot carry run_id/step_id). Pass through: no risk assessment, no second
        # Approval, no interrupt.
        if capability in pre_approved_capabilities:
            print(f"  [branch-c] SHORT-CIRCUIT pre-approved capability={capability}")
            return await handler(request)
        # ── else: identical to the real gate from here down ───────────────────

        existing = await _find_existing_approval(workspace_id, thread_id, tool_call_id, db_factory)
        if existing is not None:
            verdict = interrupt(
                {
                    "approval_id": existing.approval_id,
                    "thread_id": thread_id,
                    "capability": (existing.artifact_refs or {}).get("capability", capability),
                    "risk_level": existing.risk_level,
                }
            )
            approved = verdict == "approve" or (
                isinstance(verdict, dict) and verdict.get("decision") == "approve"
            )
            if approved:
                return await handler(request)
            return ToolMessage(
                content=json.dumps({"error": "rejected by approver", "rejected": True}),
                tool_call_id=tool_call_id,
                name=name,
                status="error",
            )

        risk = await assess_risk(capability, args)
        require_approval, approval_id = await _decide_and_maybe_persist(
            name=name,
            capability=capability,
            risk=risk,
            workspace_id=workspace_id,
            user_id=user_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            agent_name=agent_name,
            db_factory=db_factory,
        )
        if not require_approval:
            return await handler(request)

        print(f"  [branch-c] INTERRUPT (un-approved write) capability={capability}")
        verdict = interrupt(
            {
                "approval_id": approval_id,
                "thread_id": thread_id,
                "capability": capability,
                "risk_level": risk.risk_level,
            }
        )
        approved = verdict == "approve" or (
            isinstance(verdict, dict) and verdict.get("decision") == "approve"
        )
        if approved:
            return await handler(request)
        return ToolMessage(
            content=json.dumps({"error": "rejected by approver", "rejected": True}),
            tool_call_id=tool_call_id,
            name=name,
            status="error",
        )

    return trust_gate


async def _build_agent(executor, factory, workspace_id, user_id, gate, tool_name, saver):
    """Build a REAL deep agent: capability_scope (auto) → gate → dispatcher. The model
    is the deterministic _M fake (build_chat_model monkeypatched below)."""
    shells = build_tool_shells(
        [
            {
                "name": tool_name,
                "description": "spike write tool",
                "input_schema": {
                    "type": "object",
                    "properties": {"to": {"type": "string"}, "amount": {"type": "integer"}},
                },
            }
        ]
    )
    dispatcher = _build_dispatcher(user_id, workspace_id)
    # Order (outer→inner): gate OUTER of dispatcher (mirrors agent_invoker's
    # trust_gate → … → dispatcher), so an interrupt/deny stops the tool executing.
    return await build_deep_agent(
        executor,
        shells,
        workspace_id=workspace_id,
        db_factory=factory,
        extra_middleware=[gate, dispatcher],
        system_prompt="spike executor",
        checkpointer=saver,
    )


async def _count_approvals(factory, workspace_id, thread_id) -> int:
    async with factory() as db:
        n = (
            await db.execute(
                select(func.count())
                .select_from(Approval)
                .where(Approval.workspace_id == workspace_id, Approval.thread_id == thread_id)
            )
        ).scalar_one()
    return int(n)


async def _is_paused(agent, cfg) -> bool:
    """True iff the graph is paused (interrupt) — StateSnapshot.next is non-empty."""
    snap = await agent.aget_state(cfg)
    return bool(snap.next)


async def _run_scenario(agent, cfg) -> dict:
    """Invoke the agent once (durability='sync') and report interrupt vs completion.
    interrupt() with a checkpointer does NOT raise — the run pauses and ainvoke returns
    state carrying ``__interrupt__``; aget_state().next is the authoritative signal."""
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "do it"}]}, cfg, durability="sync"
    )
    interrupt_key = isinstance(result, dict) and "__interrupt__" in result
    paused = await _is_paused(agent, cfg)
    return {"paused": paused, "interrupt_key": bool(interrupt_key)}


def _proof_finalize_seam_shape() -> tuple[list[str], bool]:
    """Obs 3 (SQ3): confirm the deep step-output dict shape is EXACTLY what
    dag_runner._finalize_with_verification (+ its pre-finalize _detect_auth_required)
    consume — by feeding a synthetic executor output through both without KeyError.

    Producer contract (step_runner.run_step_via_agent_loop :440-453):
        {"status","result","tools_called","errors"}  (+ auth path adds
         error_code/provider/server/auth_required).
    """
    from src.services.dag_runner import build_verification_meta
    from src.services.execution_support import _detect_auth_required
    from src.services.verification import VerifyVerdict

    produced = {
        "status": "completed",
        "result": "sent the email",
        "tools_called": ["send_email"],
        "errors": [],
    }
    risk = RiskAssessment(
        risk_level="high", reasoning="x", reversible=False, blast_radius="external_single"
    )
    # build_verification_meta reads optional artifact_ref keys off the output dict.
    meta = build_verification_meta(WRITE_CAP, risk, VerifyVerdict.CONFIRMED, produced)
    # _detect_auth_required over a normal output → None; over the auth variant → the dict.
    normal_auth = _detect_auth_required(produced)
    auth_variant = {
        **produced,
        "status": "error",
        "error_code": "auth_required",
        "provider": "google",
        "server": "google_workspace",
        "auth_required": {"error_code": "auth_required", "provider": "google"},
    }
    detected = _detect_auth_required(auth_variant)
    ok = (
        isinstance(meta, dict)
        and normal_auth is None
        and isinstance(detected, dict)
        and detected.get("error_code") == "auth_required"
    )
    return (sorted(produced.keys()), ok)


async def run_probe() -> int:  # noqa: PLR0915 - single linear spike orchestration
    if not await _reachable():
        print("GATE_RECONCILE=SKIPPED (postgres unreachable)")
        return 1

    engine = create_async_engine(SQLA_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"

    # One fresh thread per scenario (same workspace) so Approval counts are isolated
    # and each thread_id length can be checked independently.
    thread_obs1 = make_thread_id(workspace_id)
    thread_obs2a = make_thread_id(workspace_id)
    thread_obs2b = make_thread_id(workspace_id)

    deep_interrupts = False
    preapproved_passes = False
    unapproved_gates = False
    thread_len = 0
    ws_recovers = False
    finalize_keys: list[str] = []
    finalize_ok = False

    original_build_chat_model = agent_builder.build_chat_model
    saver = MemorySaver()
    try:
        # --- Seed FK chain + two write tool defs -----------------------------
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"spike-{suffix}@example.com", display_name="spike"))
            db.add(Workspace(workspace_id=workspace_id, name="spike-ws", owner_user_id=user_id))
            await db.flush()
            db.add(
                ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name=WRITE_TOOL,
                    description="spike write email",
                    capability=WRITE_CAP,
                    requires_approval=True,
                    backend=ToolBackend.INTERNAL_MCP,
                    enabled=True,
                )
            )
            db.add(
                ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name=PAY_TOOL,
                    description="spike write payment",
                    capability=PAY_CAP,
                    requires_approval=True,
                    backend=ToolBackend.INTERNAL_MCP,
                    enabled=True,
                )
            )
            await db.commit()

        executor = _make_executor()

        # ================= Observation 1: REAL gate double-gates ==============
        print("[obs1] REAL trust_gate, AUTONOMOUS + write(email.send) — expect interrupt")
        agent_builder.build_chat_model = lambda _agent: _M(WRITE_TOOL)
        real_gate = make_trust_gate_middleware(
            authorization_source=AuthorizationSource.AUTONOMOUS,
            workspace_id=workspace_id,
            user_id=user_id,
            thread_id=thread_obs1,
            agent_name="executor",
            db_factory=factory,
            assess_risk=_gating_risk,
            # resolve_capability defaults to the REAL DB lookup over the seeded tool defs.
        )
        agent1 = await _build_agent(
            executor, factory, workspace_id, user_id, real_gate, WRITE_TOOL, saver
        )
        cfg1 = {"configurable": {"thread_id": thread_obs1}}
        r1 = await _run_scenario(agent1, cfg1)
        appr1 = await _count_approvals(factory, workspace_id, thread_obs1)
        executed_obs1 = WRITE_TOOL in EXECUTED
        deep_interrupts = r1["paused"] and appr1 == 1 and not executed_obs1
        print(
            f"[obs1] paused={r1['paused']} interrupt_key={r1['interrupt_key']} "
            f"approvals={appr1} tool_executed={executed_obs1} -> deep_interrupts={deep_interrupts}"
        )

        # ============ Observation 2a: Branch-C pre-approved short-circuit =======
        print("[obs2a] Branch-C gate, pre_approved={email.send}, write(email.send) — expect PASS")
        EXECUTED.clear()
        agent_builder.build_chat_model = lambda _agent: _M(WRITE_TOOL)
        bc_gate_a = make_branch_c_trust_gate(
            authorization_source=AuthorizationSource.AUTONOMOUS,
            workspace_id=workspace_id,
            user_id=user_id,
            thread_id=thread_obs2a,
            agent_name="executor",
            db_factory=factory,
            assess_risk=_gating_risk,
            pre_approved_capabilities={WRITE_CAP},
        )
        agent2a = await _build_agent(
            executor, factory, workspace_id, user_id, bc_gate_a, WRITE_TOOL, saver
        )
        cfg2a = {"configurable": {"thread_id": thread_obs2a}}
        r2a = await _run_scenario(agent2a, cfg2a)
        appr2a = await _count_approvals(factory, workspace_id, thread_obs2a)
        executed_2a = WRITE_TOOL in EXECUTED
        preapproved_passes = (not r2a["paused"]) and appr2a == 0 and executed_2a
        print(
            f"[obs2a] paused={r2a['paused']} approvals={appr2a} tool_executed={executed_2a} "
            f"-> preapproved_passes={preapproved_passes}"
        )

        # ============ Observation 2b: Branch-C un-approved STILL gates ==========
        print(
            "[obs2b] Branch-C gate, pre_approved={email.send}, write(payment.send) — "
            "expect INTERRUPT"
        )
        EXECUTED.clear()
        agent_builder.build_chat_model = lambda _agent: _M(PAY_TOOL)
        bc_gate_b = make_branch_c_trust_gate(
            authorization_source=AuthorizationSource.AUTONOMOUS,
            workspace_id=workspace_id,
            user_id=user_id,
            thread_id=thread_obs2b,
            agent_name="executor",
            db_factory=factory,
            assess_risk=_gating_risk,
            pre_approved_capabilities={WRITE_CAP},  # email.send only — payment.send NOT approved
        )
        agent2b = await _build_agent(
            executor, factory, workspace_id, user_id, bc_gate_b, PAY_TOOL, saver
        )
        cfg2b = {"configurable": {"thread_id": thread_obs2b}}
        r2b = await _run_scenario(agent2b, cfg2b)
        appr2b = await _count_approvals(factory, workspace_id, thread_obs2b)
        executed_2b = PAY_TOOL in EXECUTED
        unapproved_gates = r2b["paused"] and appr2b == 1 and not executed_2b
        print(
            f"[obs2b] paused={r2b['paused']} approvals={appr2b} tool_executed={executed_2b} "
            f"-> unapproved_gates={unapproved_gates}"
        )

        # ---- thread_id budget: unchanged make_thread_id(ws), ≤ 64, ws recoverable ----
        thread_len = max(len(thread_obs1), len(thread_obs2a), len(thread_obs2b))
        ws_recovers = all(
            workspace_of_thread_id(t) == workspace_id
            for t in (thread_obs1, thread_obs2a, thread_obs2b)
        )
        print(f"[thread] max_len={thread_len} (<=64: {thread_len <= 64}) ws_recovers={ws_recovers}")

        # ---- Observation 3: read-back seam shape ----
        finalize_keys, finalize_ok = _proof_finalize_seam_shape()
        print(
            f"[obs3] finalize seam consumes produced keys {finalize_keys} "
            f"without error -> {finalize_ok}"
        )

    finally:
        agent_builder.build_chat_model = original_build_chat_model
        try:
            async with factory() as db:
                await db.execute(delete(Approval).where(Approval.workspace_id == workspace_id))
                await db.execute(delete(TrustState).where(TrustState.workspace_id == workspace_id))
                await db.execute(
                    delete(ToolDefinition).where(ToolDefinition.workspace_id == workspace_id)
                )
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[cleanup] teardown failed: {exc!r}")
        await engine.dispose()

    print("=" * 64)
    print(f"DEEP_GATE_INTERRUPTS_FOR_AUTONOMOUS_WRITE={deep_interrupts}")
    print(f"PREAPPROVED_CAP_SHORT_CIRCUITS={preapproved_passes}")
    print(f"UNAPPROVED_WRITE_STILL_GATES={unapproved_gates}")
    print(f"THREAD_ID_UNCHANGED_LEN={thread_len}")
    print(f"THREAD_ID_LEQ_64={thread_len <= 64}")
    print(f"WS_RECOVERABLE_FROM_THREAD_ID={ws_recovers}")
    print(f"FINALIZE_INPUT_KEYS={finalize_keys}")
    print(f"FINALIZE_SEAM_CONSUMES_SHAPE={finalize_ok}")
    print("=" * 64)

    double_gate_observed = deep_interrupts  # obs1 proves the SECOND gate fires
    branch_c_feasible = preapproved_passes and unapproved_gates and thread_len <= 64 and ws_recovers
    ok = double_gate_observed and branch_c_feasible and finalize_ok
    print(
        "DECISION: double-gate observed = "
        f"{'YES' if double_gate_observed else 'NO'}; SQ2 -> Branch C; "
        "Branch-C mechanism = capability-set short-circuit at deep trust_gate, "
        "NO thread_id change; read-back unification -> DEFER to B4/10D (SQ3 Branch A); "
        f"_finalize_with_verification input keys = {finalize_keys}."
    )
    print(f"RESULT={'CONFIRMED' if ok else 'DISPROVEN'}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_probe()))
