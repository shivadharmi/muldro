"""Action-time confirmation gate for the deep chat single-lead (P2.1).

A SECOND ``wrap_tool_call`` interceptor, installed immediately AFTER ``trust_gate`` on
the chat single-lead path (``capability_scope → governor_audit → unavailable_server →
trust_gate → permission_gate → write_lock → dispatcher``). It implements the Claude-Code
permission model: a per-turn ``permission_mode`` of ``bypass`` / ``ask`` / ``auto`` decides
whether a WRITE pauses for the user's confirmation.

This gate is DELIBERATELY AUTH-SOURCE-INDEPENDENT — it NEVER consults
``authorization_source`` / ``is_gated_source``. On the chat single-lead path the user's
message already authorizes the turn (so ``trust_gate`` stays dormant / short-circuits);
the permission gate is a SEPARATE, action-time confirmation the user opted into via their
mode, orthogonal to provenance. It never disturbs the autonomous ``trust_gate``.

Mode × risk policy:
    * ``bypass`` — never interrupts (the user opted fully out of confirmations);
    * ``ask``    — interrupts on EVERY write, WITHOUT calling the risk classifier
                   (confirm-every-write needs no assessment — a spike-proven efficiency);
    * ``auto``   — assesses risk and interrupts only when the write is NOT reversible, has
                   an EXTERNAL/public blast radius, or is high risk (otherwise auto-executes).

Two hard rules inherited from ``trust_gate`` — do not violate:

1. ``interrupt()`` must NOT be called while a DB session/transaction is open, and (in
   ``auto`` mode) risk assessment must run with NO session open. Each DB touch here
   (``_find_existing_approval`` / ``_persist_permission_approval``) opens and CLOSES its
   own short-lived session; none is held across ``interrupt()``.

2. The ``wrap_tool_call`` gate body REPLAYS from the top on resume, so everything before
   ``interrupt()`` is idempotent: persistence is a get-or-create keyed on
   ``(workspace_id, thread_id, tool_call_id)`` (fenced by the partial-unique
   ``uq_approvals_thread_tool_call`` index), and a persisted decision is detected up front
   (the CF-2 replay short-circuit) so the resume replay goes STRAIGHT to ``interrupt()``.

``_find_existing_approval`` / ``_MAX_PERSISTED_CONTEXT_CHARS`` are REUSED from ``trust_gate``
(the same idempotency key + the same context cap) rather than re-implemented, so the two
gates can never drift on the shared contract. ``interrupt()`` and ``handler(request)`` are
intentionally NOT wrapped in try/except so a ``GraphInterrupt`` propagates normally.
"""

from __future__ import annotations

import json
import logging

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.deep_runtime.middleware.trust_gate import (
    _MAX_PERSISTED_CONTEXT_CHARS,
    _find_existing_approval,
    _get_or_create_approval,
)
from src.integrations.capabilities import is_read_only_capability
from src.services.risk_assessor import RiskAssessment

logger = logging.getLogger(__name__)

# The blast radii that reach beyond the user's own workspace. In ``auto`` mode a write with
# one of these radii is confirmation-worthy even when reversible + not high risk.
_EXTERNAL_BLAST_RADII = frozenset({"external_single", "external_multiple", "public"})

# Quotable default surfaced to the model when the user rejected without a recorded reason.
# A bare ``{"rejected": true}`` makes a real model confabulate (observed in the spike), so
# the rejection ToolMessage always carries a human-readable string.
_DEFAULT_REJECT_REASON = "the user declined this action"

# ``ask`` mode requests confirmation WITHOUT assessing risk, so the persisted/echoed
# ``risk_level`` is this sentinel rather than a ``none|low|medium|high`` value. Downstream
# consumers treat it as "not high" (info display); ``Approval.risk_level`` is an unconstrained
# String, so it stores cleanly.
_RISK_NOT_ASSESSED = "n/a"


def permission_should_interrupt(mode: str, assessment: RiskAssessment | None) -> bool:
    """Decide whether ``mode`` × ``assessment`` warrants pausing a WRITE for confirmation.

    * ``bypass`` → never (the user opted out of confirmations).
    * ``ask``    → always (confirm every write; ``assessment`` is ignored and typically None).
    * ``auto``   → only when the write is irreversible, has an external/public blast radius,
                   or is high risk. ``auto`` REQUIRES a non-None assessment; a None assessment
                   cannot be classified, so it fails CLOSED (interrupt).
    * any other mode → fails CLOSED (interrupt).
    """
    if mode == "bypass":
        return False
    if mode == "ask":
        return True
    if mode == "auto":
        if assessment is None:
            # Fail closed: cannot classify → require confirmation rather than auto-execute.
            return True
        return (
            not assessment.reversible
            or assessment.blast_radius in _EXTERNAL_BLAST_RADII
            or assessment.risk_level == "high"
        )
    # Unknown/unexpected mode → fail closed.
    return True


async def _persist_permission_approval(
    *,
    name: str,
    capability: str,
    assessment: RiskAssessment | None,
    risk_level: str,
    workspace_id: str,
    user_id: str,
    thread_id: str,
    tool_call_id: str,
    agent_name: str,
    db_factory,
    context_block: str,
    permission_mode: str,
    lead_scope,
    user_message: str = "",
) -> str:
    """Idempotently persist the pending Approval for a paused chat write and return its id.

    Opens ONE short-lived session (COMMITTED and CLOSED before the caller reaches
    ``interrupt()``) and delegates the get-or-create to the shared
    ``trust_gate._get_or_create_approval``. This path only PERSISTS (the decision was already
    made by ``permission_should_interrupt``), so — unlike the autonomous
    ``_decide_and_maybe_persist`` — the session carries no TrustEngine state and the helper's
    commit is the only write.

    In ``ask`` mode ``assessment`` is None: ``reversible`` defaults True and ``blast_radius``
    defaults ``"self"`` on the persisted ``artifact_refs`` (the confirmation was requested
    unconditionally, not because risk was assessed).
    """
    reversible = assessment.reversible if assessment else True
    blast_radius = assessment.blast_radius if assessment else "self"
    summary = assessment.reasoning if assessment else "User confirmation required (ask mode)."

    async with db_factory() as db:
        return await _get_or_create_approval(
            db,
            name=name,
            capability=capability,
            summary=summary,
            risk_level=risk_level,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            artifact_refs={
                "thread_id": thread_id,
                "tool_call_id": tool_call_id,
                "capability": capability,
                "reversible": reversible,
                "blast_radius": blast_radius,
                "tool_name": name,
                "agent_name": agent_name,
                # Bounded echo of the turn's ambient context so the resume path can
                # re-inject it (kept small to keep the approval row lean).
                "context_block": context_block[:_MAX_PERSISTED_CONTEXT_CHARS],
                # Chat-permission provenance (distinguishes these approvals from the
                # autonomous trust_gate's rows; NO migration — artifact_refs is JSONB).
                "permission_mode": permission_mode,
                "chat": True,
                "lead_scope": sorted(lead_scope),
                # A1: the ORIGINAL user message, so an approved resume can fire the
                # interaction-learner (bounded like context_block to keep the row lean).
                "user_message": user_message[:_MAX_PERSISTED_CONTEXT_CHARS],
            },
        )


def _reject_tool_message(name: str, tool_call_id: str, reason: str | None) -> ToolMessage:
    """The rejection ToolMessage carrying a QUOTABLE reason (never a bare flag)."""
    return ToolMessage(
        content=json.dumps({"error": reason or _DEFAULT_REJECT_REASON, "rejected": True}),
        tool_call_id=tool_call_id,
        name=name,
        status="error",
    )


def _blocked_tool_message(name: str, tool_call_id: str) -> ToolMessage:
    """Fail-CLOSED block for a capability-lookup error — a write must never execute ungated."""
    return ToolMessage(
        content=json.dumps(
            {"error": "capability lookup failed — blocked (fail-closed)", "blocked": True}
        ),
        tool_call_id=tool_call_id,
        name=name,
        status="error",
    )


def _is_approved(verdict) -> bool:
    return verdict == "approve" or (
        isinstance(verdict, dict) and verdict.get("decision") == "approve"
    )


def make_permission_gate_middleware(
    *,
    permission_mode: str,
    workspace_id: str,
    user_id: str,
    thread_id: str,
    agent_name: str,
    db_factory,
    assess_risk,
    resolve_capability,
    context_block: str = "",
    lead_scope=frozenset(),
    user_message: str = "",
) -> AgentMiddleware:
    """Build the action-time permission gate for one chat turn.

    ``permission_mode`` / ``workspace_id`` / ``user_id`` / ``thread_id`` / ``agent_name`` /
    ``lead_scope`` are captured in the closure — never LLM-supplied. The gate is normally
    installed only for ``permission_mode in ("ask", "auto")`` (``bypass``/``None`` never
    install it); it is nonetheless self-defending — ``bypass`` short-circuits to a no-op.

    Args:
        permission_mode: ``bypass`` | ``ask`` | ``auto`` for this turn.
        workspace_id: Tenant scope for approval find/persist.
        user_id: Authenticated user (approval owner + requester).
        thread_id: Stable LangGraph thread id — part of the idempotency key + interrupt payload.
        agent_name: Routed lead's name — recorded on the approval provenance.
        db_factory: Async-context-manager factory yielding an ``AsyncSession`` (approval
            find/persist only). Each use opens+closes a short-lived session; none is held
            across ``interrupt()``.
        assess_risk: DB-free async ``(capability, tool_input) -> RiskAssessment`` (fails closed
            to high internally). Called ONLY in ``auto`` mode; ``ask`` never invokes it.
        resolve_capability: Async ``(name) -> (lookup_ok, capability | None)``. The gate FAILS
            CLOSED on ``(False, None)`` (block the write).
        context_block: The turn's assembled ContextPack, persisted (capped) onto the Approval.
        lead_scope: The lead's ``capability_scope`` — persisted (sorted) onto the Approval so
            the resume path knows the turn's authorized envelope.
        user_message: The turn's ORIGINAL user message — persisted (capped) onto the Approval so
            an approved resume can fire the interaction-learner (parity with the non-paused tail).

    Returns:
        An ``AgentMiddleware`` exposing an async ``wrap_tool_call`` hook.
    """

    @wrap_tool_call
    async def permission_gate(request, handler):
        name = request.tool_call["name"]

        # deepagents built-ins (write_todos, ls, …) are framework scaffolding — never gated.
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)

        # bypass: the user opted fully out of confirmations. The gate is normally not even
        # installed for bypass (the seam installs only ask/auto), but this explicit no-op makes
        # the mode self-defending AND cheap (no capability/risk work) if it is ever installed —
        # keeping the docstring's contract true rather than relying on the auto-branch predicate.
        if permission_mode == "bypass":
            return await handler(request)

        tool_call_id = request.tool_call["id"]
        args = request.tool_call.get("args") or {}

        # Resolve capability via the injected resolver — its own short-lived session, closed
        # before risk/interrupt. Returns (lookup_ok, capability).
        lookup_ok, capability = await resolve_capability(name)
        if not lookup_ok:
            # Fail CLOSED: a capability-lookup error on a write must never execute ungated
            # (mirrors trust_gate / capability_scope's fail-closed deny).
            logger.warning(
                "[deep_runtime] permission_gate BLOCKED %s — capability lookup failed "
                "(fail-closed)",
                name,
            )
            return _blocked_tool_message(name, tool_call_id)
        if not capability or is_read_only_capability(capability):
            return await handler(request)

        # CF-2 replay short-circuit: on the resume REPLAY the Approval already exists — read
        # its persisted decision and go STRAIGHT to interrupt() (which returns the resume
        # value immediately), skipping the redundant risk assessment + persist that the first
        # pass already ran. Reads bypass ABOVE, so a read never pays for this extra SELECT.
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
            if _is_approved(verdict):
                return await handler(request)
            return _reject_tool_message(name, tool_call_id, existing.decision_reason)

        # First pass (no existing row). ``ask`` confirms every write WITHOUT assessing risk;
        # ``auto`` assesses and auto-executes the safe ones.
        if permission_mode == "ask":
            assessment = None
            risk_level = _RISK_NOT_ASSESSED
        else:
            # Risk assessment with NO DB session open (it may hit a slow LLM).
            assessment = await assess_risk(capability, args)
            if not permission_should_interrupt(permission_mode, assessment):
                return await handler(request)
            risk_level = assessment.risk_level

        # Idempotently persist the pending Approval inside a SEPARATE session that commits +
        # closes BEFORE interrupt().
        approval_id = await _persist_permission_approval(
            name=name,
            capability=capability,
            assessment=assessment,
            risk_level=risk_level,
            workspace_id=workspace_id,
            user_id=user_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            agent_name=agent_name,
            db_factory=db_factory,
            context_block=context_block,
            permission_mode=permission_mode,
            lead_scope=lead_scope,
            user_message=user_message,
        )

        # Suspend for confirmation. Called OUTSIDE any DB session/transaction. On resume the
        # gate body replays from the top; the durable path takes the CF-2 branch above, while
        # the in-process path re-reaches this interrupt() and returns the resume value.
        verdict = interrupt(
            {
                "approval_id": approval_id,
                "thread_id": thread_id,
                "capability": capability,
                "risk_level": risk_level,
            }
        )
        if _is_approved(verdict):
            return await handler(request)

        # Rejected on the in-process replay: re-read the (now-decided) row to quote the user's
        # reason, falling back to the clear default.
        decided = await _find_existing_approval(workspace_id, thread_id, tool_call_id, db_factory)
        reason = decided.decision_reason if decided is not None else None
        return _reject_tool_message(name, tool_call_id, reason)

    return permission_gate
