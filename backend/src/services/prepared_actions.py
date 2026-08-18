"""Deterministic replay of a PREPARED action (single-lead cutover).

THE POINT OF THIS MODULE IS THAT IT IS NOT AN AGENT. A prepared action was reviewed by the
founder as a specific tool call with specific arguments; confirming it must run *that* call.
Routing confirmation through ``GraphExecutor`` / ``DagRunner`` / ``run_step_via_deep_agent``
would run an agent that rediscovers tools and decides again — RE-DERIVING the action instead
of executing the reviewed one, which breaks the promise the review queue makes. So: read the
recorded payload, check it still means what it meant, take the same lock the middleware takes,
and execute it once.

The fail-closed checks reproduce, deterministically, what ``capability_scope`` enforces on the
agent path. This is a NEW ENFORCEMENT SITE — a prepared action must never widen authority
beyond what the original turn held. The scope it checks against is the SNAPSHOT taken at
prepare time, never a freshly derived one: a scope that has widened since (a newly connected
connector, an edited plan) must not retroactively authorise an action the founder reviewed
under narrower authority, and a scope that has since NARROWED correctly refuses.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from src.services.tool_registry import ToolRegistry
from src.services.write_lock import WriteLockContended, acquire_write_lock

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedActionResult:
    executed: bool
    result: dict | None = None
    error: str | None = None


class TruncatedPayload(ValueError):  # noqa: N818 - names the payload, not an error mode
    """The recorded payload was clipped at persist time and cannot be replayed."""


def prepared_identity_key(approval_id: str) -> str:
    """The idempotency identity of a prepared action IS its approval.

    One Approval == one reviewed action, so a double-confirm can never double-fire. This is
    deliberately NOT ``derive_identity_key`` (which keys on run/step/ordinal) — a prepared
    action has no run and no step.
    """
    return f"prepared:{approval_id}"


def _recorded_input(approval) -> dict:
    """Parse the recorded (redacted) tool_input back into a dict.

    The ``tool_input_truncated`` FLAG is authoritative, checked BEFORE parsing. A clipped
    payload is refused outright rather than left to chance: a naive character slice usually
    produces invalid JSON, but it is not guaranteed to — clip a nested object at the wrong
    byte and the remainder can still parse into a DIFFERENT, smaller action than the founder
    reviewed. Trusting the parse would make "did this execute the reviewed action?" depend on
    where the truncation landed. Trusting the flag makes it depend on nothing.
    """
    refs = approval.artifact_refs or {}
    if refs.get("tool_input_truncated"):
        raise TruncatedPayload("recorded payload was clipped at persist time")
    raw = refs.get("tool_input")
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


async def execute_prepared_action(
    approval,
    *,
    execute_tool,
    db_factory,
    redis=None,
    ledger=None,
) -> PreparedActionResult:
    """Replay one prepared action. Returns a result; never raises for a policy refusal.

    Args:
        approval: the ``Approval`` row (the caller has already checked its status).
        execute_tool: async ``(tool_name, tool_input, user_id, workspace_id) -> dict`` — the
            SAME positional contract ``muldro_tool_dispatcher`` uses.
        db_factory: async-context-manager factory yielding an ``AsyncSession``, for the
            registry lookup. Opened and closed here; never held across the execute.
        redis: Redis client for the cross-path write lock. ``None`` skips the lock, matching
            the ``write_lock`` middleware's fail-open behaviour for a missing client.
        ledger: an ``IdempotencyLedger``. ``None`` skips it (unit tests only) — the route
            always supplies one, so a double-confirm cannot double-fire.
    """
    refs = approval.artifact_refs or {}
    workspace_id = approval.workspace_id
    tool_name = refs.get("tool_name")
    recorded_capability = refs.get("capability")

    if not tool_name:
        return PreparedActionResult(False, error="prepared action has no recorded tool name")

    # 1. Re-resolve the tool from the registry by the RECORDED name.
    async with db_factory() as db:
        tool = await ToolRegistry(db, workspace_id=workspace_id or None).get_tool(tool_name)

    # 2. Fail closed on an unknown tool, a capability-less tool, or registry DRIFT.
    if tool is None:
        return PreparedActionResult(False, error=f"unknown tool '{tool_name}' — refusing")
    capability = getattr(tool, "capability", None)
    if not capability:
        return PreparedActionResult(False, error=f"tool '{tool_name}' has no capability — refusing")
    if capability != recorded_capability:
        return PreparedActionResult(
            False,
            error=(
                f"registry drift: '{tool_name}' now maps to '{capability}' but was reviewed "
                f"as '{recorded_capability}' — refusing"
            ),
        )

    # 3. Fail closed unless the capability is inside the SNAPSHOTTED scope. Never re-derived.
    snapshot = refs.get("capability_scope")
    if not isinstance(snapshot, list) or not snapshot:
        return PreparedActionResult(
            False, error="no capability scope was recorded for this action — refusing"
        )
    if capability not in snapshot:
        return PreparedActionResult(
            False,
            error=(
                f"'{capability}' is outside the authority this action was prepared under — refusing"
            ),
        )

    try:
        tool_input = _recorded_input(approval)
    except TruncatedPayload:
        return PreparedActionResult(
            False,
            error=(
                "the recorded payload was clipped when it was stored, so this action cannot "
                "be replayed exactly as reviewed — refusing"
            ),
        )
    except (json.JSONDecodeError, ValueError):
        return PreparedActionResult(
            False, error="the recorded payload could not be read back — refusing"
        )

    # 4 + 5. Same lock the middleware takes, then execute ONCE through the ledger.
    async def _execute_once() -> PreparedActionResult:
        if ledger is None:
            result = await execute_tool(tool_name, tool_input, approval.user_id, workspace_id)
            return PreparedActionResult(True, result=result)

        identity_key = prepared_identity_key(approval.approval_id)
        outcome = await ledger.reserve(
            workspace_id=workspace_id,
            run_id=None,
            step_id=None,
            capability=capability,
            identity_key=identity_key,
        )
        if outcome.already_done:
            logger.info("[prepared] %s already executed — not re-firing", approval.approval_id)
            return PreparedActionResult(True, result=outcome.result)
        if outcome.in_flight_conflict:
            return PreparedActionResult(
                False, error="a prior attempt is still in flight — not re-fired"
            )
        result = await execute_tool(tool_name, tool_input, approval.user_id, workspace_id)
        is_err = isinstance(result, dict) and (result.get("error") or result.get("is_error"))
        if is_err:
            await ledger.mark_failed(outcome.ledger_id)
            return PreparedActionResult(False, result=result, error=str(result.get("error")))
        await ledger.record_success(outcome.ledger_id, result)
        return PreparedActionResult(True, result=result)

    if redis is None:
        return await _execute_once()
    try:
        async with acquire_write_lock(redis, workspace_id, capability):
            return await _execute_once()
    except WriteLockContended:
        return PreparedActionResult(
            False, error="another write to this capability is in progress — try again"
        )
