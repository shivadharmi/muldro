"""The idempotency DI seam.

`make_idempotent_execute_tool_fn` wraps the injected `execute_tool_fn`. Only the
AUTONOMOUS path (step_runner) installs it; the chat path injects the plain
execute_tool_fn, so chat is untouched (idempotency is an autonomous-path property).

Write capabilities go through the ledger; read capabilities bypass it entirely.
The identity key is derived per capability (semantic fields / native token /
positional), captured at first attempt and reproduced on resume — so an
LLM-recomposed payload cannot double-fire.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import count

from src.services.idempotency.identity import derive_identity_key
from src.services.idempotency.ledger import IdempotencyLedger

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IdempotencyContext:
    ledger: IdempotencyLedger
    run_id: str
    step_id: str
    workspace_id: str
    db_factory: object


async def _resolve_capability_is_write(
    tool_name: str, db_factory, workspace_id: str
) -> tuple[str | None, bool]:
    """Resolve (capability, is_write) for a tool via the registry. Injectable
    (see make_idempotent_execute_tool_fn) so tests can supply a fake."""
    from src.services.capability_resolver import CapabilityResolver
    from src.services.tool_registry import ToolRegistry

    async with db_factory() as db:
        registry = ToolRegistry(db, workspace_id=workspace_id or None)
        tool = await registry.get_tool(tool_name)
        capability = getattr(tool, "capability", None) if tool else None
        if not capability:
            return None, False
        is_write = await CapabilityResolver(
            db, workspace_id=workspace_id or ""
        ).is_write_capability(capability)
        return capability, is_write


def make_idempotent_execute_tool_fn(
    inner_execute_tool_fn, ctx: IdempotencyContext, *, resolve_capability=None
):
    """Return an execute_tool_fn that applies the idempotency ledger to writes."""
    resolve = resolve_capability or _resolve_capability_is_write
    ordinal_counter = count()

    async def _idempotent_execute(tool_name, tool_input, *, user_id, workspace_id):
        capability, is_write = await resolve(
            tool_name, ctx.db_factory, workspace_id or ctx.workspace_id
        )
        if not is_write or capability is None:
            return await inner_execute_tool_fn(
                tool_name, tool_input, user_id=user_id, workspace_id=workspace_id
            )

        ordinal = next(ordinal_counter)
        identity_key = derive_identity_key(
            capability, tool_input or {}, run_id=ctx.run_id, step_id=ctx.step_id, ordinal=ordinal
        )
        outcome = await ctx.ledger.reserve(
            workspace_id=workspace_id or ctx.workspace_id,
            run_id=ctx.run_id,
            step_id=ctx.step_id,
            capability=capability,
            identity_key=identity_key,
        )
        if outcome.already_done:
            logger.info("[idempotency] %s SKIP (already completed) key=%s", tool_name, identity_key)
            return outcome.result
        if outcome.in_flight_conflict:
            logger.warning(
                "[idempotency] %s NOT re-fired — prior attempt in-flight key=%s",
                tool_name,
                identity_key,
            )
            return {
                "error": (
                    "idempotency: prior attempt in-flight; not re-fired (awaiting verification)"
                ),
                "idempotent_uncertain": True,
            }

        # Fire the effect, then record — a second, non-atomic transaction. If
        # record_success/mark_failed fails after the effect fired, the row stays
        # in_flight, so on resume it is treated as in_flight_conflict (fail-closed:
        # never double-fires). A read-back (a later step) is what unblocks such rows.
        result = await inner_execute_tool_fn(
            tool_name, tool_input, user_id=user_id, workspace_id=workspace_id
        )
        is_err = isinstance(result, dict) and (result.get("error") or result.get("is_error"))
        if is_err:
            await ctx.ledger.mark_failed(outcome.ledger_id)
        else:
            await ctx.ledger.record_success(outcome.ledger_id, result)
        return result

    return _idempotent_execute
