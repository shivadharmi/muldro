"""Shared approval-persistence surface for the two write gates (trust_gate, permission_gate).

``trust_gate`` (autonomous path) and ``permission_gate`` (chat path) are SIBLING gates: both
pause a turn for a human by persisting a pending ``Approval`` row, and both need the SAME
idempotency key, the SAME context-length cap, and the SAME redaction rule for the tool-call
payload they echo onto ``artifact_refs``. Before this module existed, ``permission_gate``
imported three underscore-prefixed "privates" directly out of ``trust_gate`` — a name that
claims to be private while actually being another module's API, and a coupling that would
have only grown as more shared persistence logic accreted onto whichever gate happened to
write it first. This module is the alternative: a peer that both gates depend on, so neither
gate's internals leak into the other's.

Holds, verbatim from their original home in ``trust_gate``:
    * ``_MAX_PERSISTED_CONTEXT_CHARS`` — the shared bound for any string persisted onto
      ``artifact_refs`` (context block, tool input).
    * ``redact_tool_input`` (+ its ``REDACTED`` / ``_is_secret_key`` / ``_redact`` helpers) —
      the deny-list redaction + bounding rule for a persisted tool-call payload.
    * ``_find_existing_approval`` — the CF-2 replay-detection read, keyed on
      ``(workspace_id, thread_id, tool_call_id)``.
    * ``_get_or_create_approval`` — the replay-safe idempotent persist, keyed the same way.

Plus one function that is NEW here (not a move): ``build_legibility_refs``, the four
``artifact_refs`` keys both gates persist identically. The keys that genuinely differ between
the two gates (``permission_gate`` alone carries ``chat`` / ``permission_mode`` / ``lead_scope``
/ ``user_message``) stay inline in each gate — only the truly common four moved.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models.approvals import Approval
from src.services.approval_service import create_approval

# Cap the persisted ContextPack echoed onto the Approval's artifact_refs. artifact_refs is
# JSONB (unbounded), but the context block is re-injected on resume and kept bounded so a
# large ambient context can never bloat the approval row.
_MAX_PERSISTED_CONTEXT_CHARS = 8000

# Invariant 9 (single-lead cutover): the persisted tool_input must never carry a secret and
# must never bloat the Approval row. Substring match, case-insensitive, on the KEY name — a
# deny-list of names rather than a value heuristic, so it cannot be fooled by an odd-looking
# value and cannot silently redact a legitimate field.
REDACTED = "[redacted]"
_REDACTED_KEY_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "api_key",
    "authorization",
    "credential",
)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _REDACTED_KEY_SUBSTRINGS)


def _redact(value):
    """Recursively replace deny-listed keys' values with ``REDACTED``.

    Recurses into dicts, lists, and TUPLES. Tuples matter because ``json.dumps`` serialises
    them identically to lists, so a tuple that skipped redaction would be indistinguishable
    in the persisted payload from a list that did not. A matched key's value is replaced
    WHOLE rather than recursed into, so a secret nested under a secret-named parent cannot
    survive via partial recursion.
    """
    if isinstance(value, dict):
        return {k: (REDACTED if _is_secret_key(str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def redact_tool_input(args: dict | None) -> tuple[str, bool]:
    """Return ``(json_payload, truncated)`` for persistence onto ``artifact_refs``.

    Deny-listed keys are redacted (recursively), the result is JSON-serialised, and the
    string is capped at the SAME ``_MAX_PERSISTED_CONTEXT_CHARS`` both gates already use for
    ``context_block`` — one constant, so the two bounds cannot drift. ``truncated`` is
    returned separately so the queue can SAY the payload was clipped rather than showing a
    lie. ``default=repr`` keeps a non-JSON-serialisable argument from raising inside a gate.
    """
    payload = json.dumps(_redact(args or {}), default=repr)
    if len(payload) > _MAX_PERSISTED_CONTEXT_CHARS:
        return payload[:_MAX_PERSISTED_CONTEXT_CHARS], True
    return payload, False


def build_legibility_refs(
    tool_input: dict | None,
    capability_scope,
    presence: str,
    *,
    prepared: bool = False,
) -> dict:
    """The ``artifact_refs`` keys BOTH gates persist identically.

    Kept here rather than inlined in each gate for the same reason
    ``_get_or_create_approval`` is: the two gates must not drift. The remaining keys in each
    gate's ``artifact_refs`` genuinely differ (``permission_gate`` alone carries ``chat`` /
    ``permission_mode`` / ``lead_scope`` / ``user_message``), so only the common four move.

    ``prepared`` marks a row the single-lead review queue owns (single-lead cutover). Left out
    of the dict entirely when False — see the comment at the call site below for why.
    """
    persisted_input, input_truncated = redact_tool_input(tool_input)
    refs = {
        "tool_input": persisted_input,
        "tool_input_truncated": input_truncated,
        "capability_scope": sorted(capability_scope),
        # EFFECTIVE, not nominal. A turn the founder was actively watching is recorded here as
        # ``absent`` when the downgrade came from a missing durable checkpointer rather than
        # from nobody being there (``_resolve_effective_presence``). The review queue will read
        # this as provenance, so the key says what the value actually is.
        "effective_presence": presence,
    }
    if prepared:
        # Marks a row the review queue owns. Kept out of the dict entirely when False rather
        # than written as `"prepared": False`, so a queue query can key on presence-of-key and
        # every pre-existing approval row stays correctly excluded.
        refs["prepared"] = True
    return refs


# ``Approval.approval_type`` for a write that was recorded rather than executed. The review
# queue finds these rows by this exact value, so it is a constant, not an inline literal.
PREPARED_APPROVAL_TYPE = "prepared_action"


def prepared_approval_overrides(
    prepared: bool, ttl_days: int
) -> tuple[str | None, datetime | None]:
    """Return the ``(approval_type, expires_at)`` overrides for ``_get_or_create_approval``.

    ``(None, None)`` when the write is being interrupted rather than prepared — which
    ``_get_or_create_approval`` reads as "keep today's defaults", so the live-approval path is
    untouched. Returning a pair rather than mutating keeps the two gates' call sites symmetric
    and makes the prepared/live distinction one decision instead of two scattered conditionals.
    """
    if not prepared:
        return (None, None)
    return (
        PREPARED_APPROVAL_TYPE,
        datetime.now(timezone.utc) + timedelta(days=ttl_days),
    )


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
    approval_type: str | None = None,
    expires_at: datetime | None = None,
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

    ``approval_type`` / ``expires_at`` default to today's values (``tool:<name>`` and
    ``create_approval``'s 24h). The PREPARE path overrides both: ``prepared_action`` so the
    review queue can find these rows, and a longer TTL because prepared work is reviewed on
    the founder's schedule, not the turn's.
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
        approval_type=approval_type or f"tool:{name}",
        title=f"Approve: {capability}",
        summary=summary,
        risk_level=risk_level,
        requested_by=user_id,
        run_id=None,
        step_id=None,
        artifact_refs=artifact_refs,
        expires_at=expires_at,
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
