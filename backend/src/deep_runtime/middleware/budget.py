"""Budget-recording middleware for the Deep Agents runtime.

Re-homes the legacy ``agent_loop`` end-of-loop authoritative cost record onto a
LangChain ``after_model`` hook. After each model call, the hook reads the last AI
message's ``usage_metadata`` and writes one ``TokenUsage`` row via
``BudgetTracker.record_usage`` (the single source of truth for cost).

**Why ``after_model`` (not ``wrap_model_call``):** the legacy record is a pure
post-call side effect — we only need per-call usage + an async DB write, never to
mutate the request/response. ``after_model`` gives us exactly the final state with
the assistant message attached, and (when decorated on an ``async def``) exposes
an async ``aafter_model`` hook so the DB write needs no thread hop.
``wrap_model_call`` would force us to wrap and re-yield the response for no gain.

**Best-effort, by design:** a budget write must NEVER break the agent loop. Every
failure (including ``record_usage`` raising ``ValueError`` for a missing
``workspace_id``) is caught and logged; the hook always returns ``None``.

TODO(phase2): per-tool token attribution. The former ``agent_loop`` also
wrote per-tool ``TokenUsage`` rows with ``trigger=f"tool:{tool_name}"``. A
faithful per-tool token *split* needs per-round tool context that is not
available inside ``after_model`` alone, so it is intentionally omitted here. It is
analytics, not a safety/cost-accuracy concern (the authoritative per-call record
below already captures the full cost), so we do not fake a split.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, after_model

logger = logging.getLogger(__name__)


def make_budget_middleware(
    *,
    agent_name: str,
    model: str,
    workspace_id: str,
    db_factory: Callable[[], Any],
    budget: Any,
    trace_id: str | None = None,
    trigger: str = "chat",
) -> AgentMiddleware:
    """Build an ``after_model`` middleware that records per-model-call token usage.

    Args:
        agent_name: Name of the routed sub-agent (for attribution rows).
        model: Model id used for the call (drives pricing in ``BudgetTracker``).
        workspace_id: Tenant scope — REQUIRED. ``record_usage`` raises without it,
            and that contract is preserved (we always pass it through).
        db_factory: Async context-manager factory; used as
            ``async with db_factory() as db: ...`` for the write + commit.
        budget: A ``BudgetTracker`` (or compatible) exposing
            ``async record_usage(db, *, agent_name, model, input_tokens,
            output_tokens, cache_creation_input_tokens, cache_read_input_tokens,
            trigger, trace_id, workspace_id)``.
        trace_id: Optional trace id to attach to the usage row.
        trigger: Usage trigger label (defaults to ``"chat"``).

    Returns:
        An ``AgentMiddleware`` whose async ``aafter_model`` hook records usage.
    """

    @after_model(name="MuldroBudgetMiddleware")
    async def _record_budget(state: dict[str, Any], runtime: Any) -> None:
        """Record token usage for the most recent model call (side effect only)."""
        try:
            messages = state.get("messages") or []
            if not messages:
                return

            usage = getattr(messages[-1], "usage_metadata", None)
            if not usage:
                # No usage metadata => no API call to bill (or a non-AI message).
                return

            details = usage.get("input_token_details") or {}

            async with db_factory() as db:
                await budget.record_usage(
                    db,
                    agent_name=agent_name,
                    model=model,
                    input_tokens=usage.get("input_tokens", 0) or 0,
                    output_tokens=usage.get("output_tokens", 0) or 0,
                    cache_creation_input_tokens=details.get("cache_creation", 0) or 0,
                    cache_read_input_tokens=details.get("cache_read", 0) or 0,
                    trigger=trigger,
                    trace_id=trace_id,
                    workspace_id=workspace_id,
                )
                await db.commit()
        except Exception as e:  # noqa: BLE001 — best-effort: never break the loop
            logger.error("Failed to record token usage (budget middleware): %s", e)
        return None

    return _record_budget
