"""THE ONE approval gate for the deep chat runtime (Step 6B).

A ``wrap_tool_call`` interceptor placed BETWEEN capability_scope (OUTER) and
muldro_tool_dispatcher (INNER) — the composed chain is
``capability_scope → trust_gate → dispatcher``. By the time this gate runs,
capability_scope has ALREADY authorized that the tool is inside the agent's
``capability_scope``, so the gate never re-checks scope; it only decides *approval*.

Gate policy (LOCKED for Step 6B, activated in 6C):
    The gate is DORMANT on real chat traffic. When ``authorization_source ==
    "direct_user_request"`` (live chat today) it SHORT-CIRCUITS — the user's message IS
    the authorization for that turn (the two-execution-paths invariant). It evaluates
    trust×risk + an IRREVERSIBLE hard override ONLY for other provenance
    (autonomous / headless / custom). Do NOT add a trust gate to the direct-chat path
    beyond this short-circuit.

Two hard rules learned from live spikes in this repo — do not violate:

1. ``interrupt()`` must NOT be called while a DB session/transaction is open, and the
   risk assessment must NOT run with a session open either. ``interrupt()`` suspends the
   coroutine for the ENTIRE approval round-trip (minutes/hours); holding a Postgres
   connection across it would exhaust the pool, and risk assessment may hit a slow LLM.
   So: resolve capability in its own short-lived session; assess risk with NO session
   open; then open a SEPARATE session that persists+**commits** the Approval and closes
   BEFORE ``interrupt()`` is reached.

2. The ``wrap_tool_call`` gate body REPLAYS from the top on resume — proven by
   ``spikes/deep_stream/interrupt_replay_side_effect_probe.py`` (PRE-interrupt code runs
   TWICE, the tool runs once). A naive ``create_approval`` before ``interrupt()`` would
   therefore create a DUPLICATE pending Approval on every resume. Approval persistence is
   IDEMPOTENT: a get-or-create keyed on ``(workspace_id, thread_id, tool_call_id)`` with
   NO status filter (the resume path marks the original approved/rejected BEFORE resuming
   the graph, so filtering by ``pending`` would miss it and duplicate the row).

``interrupt()`` and ``handler(request)`` are intentionally NOT wrapped in try/except so a
``GraphInterrupt`` (and any handler error) propagates normally.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.deep_runtime.authorization import is_gated_source
from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.integrations.capabilities import is_read_only_capability
from src.models.approvals import Approval
from src.services.approval_service import create_approval
from src.services.tool_registry import ToolRegistry
from src.services.trust_engine import TrustEngine
from src.services.verification.predicate import is_write_verification_required

logger = logging.getLogger(__name__)

# Cap the persisted ContextPack echoed onto the Approval's artifact_refs. artifact_refs is
# JSONB (unbounded), but the context block is re-injected on resume and kept bounded so a
# large ambient context can never bloat the approval row.
_MAX_PERSISTED_CONTEXT_CHARS = 8000


async def _resolve_tool_def(name: str, workspace_id: str, db_factory) -> tuple[bool, Any]:
    """Resolve *name* → its ``ToolDefinition`` via ONE short-lived registry lookup.

    Mirrors ``capability_scope._is_in_scope``: ``ToolRegistry(db, workspace_id or None)``
    → ``get_tool(name)``. Returns ``(lookup_ok, tool_def_or_None)``:

    * ``(True, <ToolDefinition>)`` — the tool is known (caller projects out whatever field it
      needs: ``.capability`` / ``.enabled`` / ``.risk_level``);
    * ``(True, None)`` — the lookup SUCCEEDED but the tool is unknown;
    * ``(False, None)`` — the lookup ERRORED. Callers decide their OWN fail policy over this:
      trust_gate fails CLOSED (block), governor_audit + write_lock fail OPEN (allow / no lock).

    This is the SINGLE per-turn ToolDef resolution shared (memoized in the invoker) by
    governor_audit + trust_gate + write_lock (6C #1) — three consumers, one lookup, one
    session. The session is opened and CLOSED here, never held across risk assessment or
    ``interrupt()`` (the memoized value is a plain ToolDef, not an open session).
    """
    try:
        async with db_factory() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)
            tool = await registry.get_tool(name)
    except Exception:
        logger.warning(
            "[deep_runtime] tool-def lookup failed for %s — caller decides fail policy",
            name,
            exc_info=True,
        )
        return (False, None)
    return (True, tool)


async def _resolve_capability(name: str, workspace_id: str, db_factory) -> tuple[bool, str | None]:
    """Resolve *name* → capability via ONE short-lived registry lookup.

    Thin ``.capability`` projection over :func:`_resolve_tool_def`, kept as a stable module
    function so existing callers/tests that patch ``_resolve_capability`` keep working.
    Returns ``(lookup_ok, capability)``:

    * ``(True, "<capability>")`` — resolved (may be a read or a write capability);
    * ``(True, None)`` — the lookup SUCCEEDED but the tool is unknown / has no capability.
      This cannot happen on the gated path (the outer ``capability_scope`` guard already
      denied such tools), so the caller falls through;
    * ``(False, None)`` — the lookup ERRORED. The caller MUST fail CLOSED (block) — a gated
      write must never execute ungated on a transient DB/registry failure, mirroring
      ``capability_scope``'s fail-closed deny.
    """
    ok, tool = await _resolve_tool_def(name, workspace_id, db_factory)
    return (ok, getattr(tool, "capability", None) if tool else None)


async def _find_existing_approval(workspace_id, thread_id, tool_call_id, db_factory):
    """Return the Approval already persisted for this (workspace, thread, tool_call) tuple, or
    None. Used to detect the RESUME REPLAY: the gate body re-runs on resume, and if the approval
    already exists we skip the redundant risk assessment + trust evaluation and go straight to
    ``interrupt()`` (which returns the resume value immediately).

    Keyed on the promoted COLUMNS (CF-3) fenced by the partial UNIQUE index
    ``uq_approvals_thread_tool_call``. NO status filter: the resume path marks the original
    approved/rejected BEFORE resuming the graph, so a pending-only filter would miss it. The
    session is opened and CLOSED here, never held across ``interrupt()``.
    """
    async with db_factory() as db:
        stmt = select(Approval).where(
            Approval.workspace_id == workspace_id,
            Approval.thread_id == thread_id,
            Approval.tool_call_id == tool_call_id,
        )
        result = await db.execute(stmt)
        return result.scalars().first()


async def _get_or_create_approval(
    db,
    *,
    name: str,
    capability: str,
    summary: str | None,
    risk_level: str,
    user_id: str,
    workspace_id: str,
    thread_id: str,
    tool_call_id: str,
    artifact_refs: dict,
) -> str:
    """Idempotent get-or-create of the pending Approval on an ALREADY-OPEN session, returning
    its id. The replay-safe persist shared by the autonomous ``trust_gate`` and the chat
    ``permission_gate`` (a DELIBERATE TWIN collapsed onto one helper).

    The CALLER owns the session so the autonomous path can keep ``TrustEngine.evaluate`` in the
    SAME transaction as the create+commit (evaluate may leave an uncommitted first-use
    ``TrustState`` INSERT that only the trailing commit persists — opening a fresh session here
    would strand it). Keyed on the promoted COLUMNS ``(workspace_id, thread_id, tool_call_id)``
    (fenced by the partial UNIQUE index ``uq_approvals_thread_tool_call``) with NO status filter:
    the gate body replays on resume and the resume path may already have marked the row
    approved/rejected, so a pending-only filter would miss it and duplicate. On a lost create
    race the ``IntegrityError`` rolls back and re-selects the winner (fail LOUD if still absent).
    """
    stmt = select(Approval).where(
        Approval.workspace_id == workspace_id,
        Approval.thread_id == thread_id,
        Approval.tool_call_id == tool_call_id,
    )
    result = await db.execute(stmt)
    existing = result.scalars().first()
    if existing is not None:
        return existing.approval_id

    approval = await create_approval(
        db,
        user_id=user_id,
        workspace_id=workspace_id,
        approval_type=f"tool:{name}",
        title=f"Approve: {capability}",
        summary=summary,
        risk_level=risk_level,
        requested_by=user_id,
        run_id=None,
        step_id=None,
        artifact_refs=artifact_refs,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(stmt)
        existing = result.scalars().first()
        if existing is None:
            raise
        return existing.approval_id
    return approval.approval_id


async def _decide_and_maybe_persist(
    *,
    name: str,
    capability: str,
    risk,
    workspace_id: str,
    user_id: str,
    thread_id: str,
    tool_call_id: str,
    agent_name: str,
    db_factory,
    context_block: str = "",
) -> tuple[bool, str | None]:
    """Decide whether *this* tool call needs approval and, if so, persist the Approval.

    Runs entirely inside ONE short-lived session that is COMMITTED and CLOSED before the
    caller reaches ``interrupt()``. Returns ``(require_approval, approval_id)`` where
    ``approval_id`` is ``None`` when no approval is required.

    Approval is required when EITHER the trust matrix says ``approval_required`` OR the
    IRREVERSIBLE union override (``is_write_verification_required``) fires — the override
    forces approval even when the matrix alone would auto-execute. Persistence is
    idempotent (get-or-create, no status filter) because the gate body replays on resume.
    """
    async with db_factory() as db:
        decision = await TrustEngine(db, workspace_id).evaluate(capability, risk, workspace_id)
        # Fail closed: only the two explicit auto-execute verdicts skip approval; any other
        # value (approval_required, or an unexpected/future decision) requires approval.
        matrix_requires = decision.decision not in ("auto_execute_notify", "auto_execute_silent")
        irreversible_override = is_write_verification_required(capability, risk)
        require_approval = matrix_requires or irreversible_override
        if not require_approval:
            return (False, None)

        # Persist on THIS open session so TrustEngine.evaluate (above) and the create+commit
        # stay in ONE transaction — the shared replay-safe get-or-create (see helper docstring).
        approval_id = await _get_or_create_approval(
            db,
            name=name,
            capability=capability,
            summary=(getattr(decision, "justification", None) or risk.reasoning),
            risk_level=risk.risk_level,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            artifact_refs={
                "thread_id": thread_id,
                "tool_call_id": tool_call_id,
                "capability": capability,
                "reversible": risk.reversible,
                "blast_radius": risk.blast_radius,
                "tool_name": name,
                "agent_name": agent_name,
                # CF-1: echo the assembled ContextPack so the resume path can re-inject the
                # original turn's ambient context (bounded to keep the approval row small).
                "context_block": context_block[:_MAX_PERSISTED_CONTEXT_CHARS],
            },
        )
        return (True, approval_id)


def make_trust_gate_middleware(
    *,
    authorization_source: str,
    workspace_id: str,
    user_id: str,
    thread_id: str,
    agent_name: str,
    db_factory,
    assess_risk,
    resolve_capability=None,
    context_block: str = "",
    pre_approved_capabilities: frozenset[str] = frozenset(),
) -> AgentMiddleware:
    """Build THE approval gate for one turn.

    ``authorization_source`` / ``workspace_id`` / ``user_id`` / ``thread_id`` /
    ``agent_name`` are captured in the closure — never LLM-supplied.

    Args:
        authorization_source: Provenance literal captured at the seam. When
            ``"direct_user_request"`` the gate is dormant (short-circuits).
        workspace_id: Tenant scope for registry/trust/approval work.
        user_id: Authenticated user for this turn (approval owner + requester).
        thread_id: Stable LangGraph thread id — part of the idempotency key and echoed
            in the interrupt payload so the resume path can correlate.
        agent_name: The routed sub-agent's name — recorded on the approval provenance.
        db_factory: Async-context-manager factory yielding an ``AsyncSession``. Used ONLY for
            approval find/persist (``_find_existing_approval`` / ``_decide_and_maybe_persist``);
            capability resolution is delegated to ``resolve_capability``. Each use opens and
            closes a short-lived session; none is held across ``interrupt()``.
        assess_risk: DB-free async callable ``(capability, tool_input) -> RiskAssessment``
            (fails closed to high internally).
        resolve_capability: Async ``(name) -> (lookup_ok, capability | None)``. Injected by the
            invoker as a projection over the per-turn SHARED ``_resolve_tool_def`` (6C #1) so
            governor_audit + trust_gate + write_lock resolve each tool ONCE. Defaults to a
            standalone ``_resolve_capability`` closure over ``db_factory`` when not injected.
            The gate FAILS CLOSED on ``(False, None)`` regardless of who supplies it.
        context_block: The assembled ContextPack for this turn. Persisted (capped) onto the
            Approval's ``artifact_refs`` at pause time so the resume path can re-inject the
            original turn's ambient context (CF-1). Empty on the dormant direct-chat path.
        pre_approved_capabilities: Step 10C (SQ2 Branch C) — capabilities already gated at the
            STEP level by ``dag_runner``'s durable TrustEngine gate. A tool whose capability is
            in this set passes through the tool-call gate WITHOUT re-prompting (the autonomous
            step seam passes ``{step.capability}``); an UN-approved within-step capability still
            falls through to the gate. Defaults to the empty frozenset, so chat/resume callers
            are byte-identical to before this param existed.

    Returns:
        An ``AgentMiddleware`` exposing an async ``wrap_tool_call`` hook.
    """
    if resolve_capability is None:

        async def resolve_capability(name: str) -> tuple[bool, str | None]:
            return await _resolve_capability(name, workspace_id, db_factory)

    @wrap_tool_call
    async def trust_gate(request, handler):
        name = request.tool_call["name"]

        # deepagents built-ins (write_todos, ls, …) are framework scaffolding — never gated.
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)

        # DORMANT direct-chat path: the user's message IS the authorization. Nothing else
        # runs — no DB, no risk assessment.
        if not is_gated_source(authorization_source):
            return await handler(request)

        tool_call_id = request.tool_call["id"]
        args = request.tool_call.get("args") or {}

        # Resolve capability via the injected (per-turn shared) resolver — its own short-lived
        # session, closed before risk/interrupt. Returns (lookup_ok, capability).
        lookup_ok, capability = await resolve_capability(name)
        if not lookup_ok:
            # Fail CLOSED: a capability-lookup error on a gated write must never execute
            # ungated (mirrors capability_scope's fail-closed deny). Block with an error.
            logger.warning(
                "[deep_runtime] trust_gate BLOCKED %s — capability lookup failed (fail-closed)",
                name,
            )
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

        # Step 10C (SQ2 Branch C): a capability already gated at the STEP level (dag_runner's
        # durable TrustEngine gate) must NOT be re-prompted at the tool-call level. The autonomous
        # step seam passes {step.capability}; chat/resume pass the empty default -> byte-neutral. An
        # UN-approved within-step capability still falls through to the gate below (not dead-wired).
        if capability in pre_approved_capabilities:
            return await handler(request)

        # CF-2: on the resume REPLAY the Approval already exists — read its persisted decision
        # and go STRAIGHT to interrupt() (which returns the resume value immediately), skipping
        # the redundant risk assessment + trust evaluation that the FIRST pass already ran and
        # persisted. Reads bypass ABOVE, so a read never pays for this extra SELECT.
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

        # First pass (no existing row): assess + decide + persist, then interrupt. The
        # get-or-create inside _decide_and_maybe_persist stays as the replay-safe create with
        # its IntegrityError re-select — the early check above only handles the REPLAY case.
        # Risk assessment with NO DB session open (it may hit a slow LLM).
        risk = await assess_risk(capability, args)

        # Decide + (idempotently) persist inside a SEPARATE session that commits + closes.
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
            context_block=context_block,
        )

        # Auto-execute path (both auto_execute_notify and auto_execute_silent land here;
        # notification delivery is out of scope for this gate).
        if not require_approval:
            return await handler(request)

        # Suspend for approval. Called OUTSIDE any DB session/transaction. On resume the
        # gate body replays from the top; this returns the resume value instead of pausing.
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
