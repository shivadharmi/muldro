"""Step 10B Task 3b: ``ShadowRunner`` — the sampled, async, isolated harness that runs
the NON-authoritative agent runtime alongside the authoritative one and diffs their
read-only decision outputs via ``DivergenceComparator``, emitting ``shadow_divergence``.

Runtime-agnostic by design (the plan reuses it for the autonomous path in 10C): this
module knows nothing about SSE frames, A2UI surfaces, or Plan records — it only needs
an ``AgentInvoker``-shaped collaborator (``run_shadow_turn``), a real tool executor to
wrap, a DB-session-factory provider, and (optionally) a budget tracker. Today's only
caller is the chat seam (``ChatProcessor``).

Default OFF (``settings.shadow_sample_rate=0.0``): the sampling check below returns
before doing anything, and the chat-seam spawn site is ADDITIONALLY guarded by the same
flag (``shadow_sample_rate > 0``) so the live path never even schedules the background
task — byte-neutral by default, on two independent guards.

Isolation is the point of this module: a shadow run that raises is caught here (logged
as a warning), never propagating to or altering the authoritative turn that spawned it
as a fire-and-forget background task.
"""

from __future__ import annotations

import logging
import random

from src.orchestrator.divergence import DivergenceComparator, ShadowDecision
from src.orchestrator.shadow_tool_executor import ShadowToolExecutor
from src.services.metrics_service import MetricsService

logger = logging.getLogger(__name__)


class _IntentRecordingShadowExecutor:
    """Thin ``ExecuteToolFn``-shaped wrapper around a ``ShadowToolExecutor`` that
    derives write-intents from the shadow's OWN suppression signal — no duplicate
    capability classification lives here. Every suppressed call is recorded as
    ``"{capability}:{tool_name}"`` into ``self.write_intents``; the inner result is
    returned unchanged either way (read passthrough or write suppression)."""

    def __init__(self, shadow: ShadowToolExecutor) -> None:
        self._shadow = shadow
        self.write_intents: set[str] = set()

    async def execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
        result = await self._shadow.execute_tool(tool_name, tool_input, user_id, workspace_id)
        if isinstance(result, dict) and result.get("shadow_suppressed"):
            self.write_intents.add(f"{result.get('capability')}:{tool_name}")
        return result


async def _resolve_capability(tool_name: str, db_factory, workspace_id: str) -> str | None:
    """Async ``(name) -> capability | None`` resolver over a throwaway session, used
    ONLY for the shadow's own read/write classification inside ``ShadowToolExecutor``.
    Mirrors ``src/services/idempotency/wrapper.py::_resolve_capability_is_write`` but
    returns just the capability string — ``ShadowToolExecutor`` derives read/write
    itself via ``is_read_only_capability``, so there is no second classification here."""
    from src.services.tool_registry import ToolRegistry

    async with db_factory() as db:
        tool = await ToolRegistry(db, workspace_id=workspace_id or None).get_tool(tool_name)
        return getattr(tool, "capability", None) if tool else None


class ShadowRunner:
    """Sampled, async, isolated shadow-compare orchestration. Holds no per-call state —
    safe to construct once at the composition root and share across turns."""

    def __init__(
        self,
        invoker,
        settings,
        tool_executor,
        db_factory_provider,
        budget=None,
    ) -> None:
        self._invoker = invoker
        self._settings = settings
        self._tool_executor = tool_executor
        self._db_factory_provider = db_factory_provider
        # Forward scaffolding for the 10C/10D budget gate (see maybe_run_shadow's
        # TODO). Intentionally unused today — the default-off sample rate is the
        # primary guard until BudgetTracker exposes a cheap is-exhausted check.
        self._budget = budget

    async def maybe_run_shadow(
        self,
        *,
        agent_name: str,
        message: str,
        user_id: str,
        workspace_id: str,
        authoritative_decision: ShadowDecision,
    ) -> None:
        """Sample, build the opposite-runtime shadow decision, diff it against the
        authoritative one, and record any divergences. Never raises — the ENTIRE
        operation (run + compare + emit) is isolated (logged) so it can never affect
        the caller's fire-and-forget turn."""
        # Single guard: rate<=0 short-circuits (byte-neutral default), and for a
        # positive rate the sample draw decides. random.random() is [0.0, 1.0), so
        # rate=1.0 always fires and rate=0.5 fires ~half the turns.
        # TODO(10D): tighten the budget gate — inspect self._budget once BudgetTracker
        # exposes a cheap is-exhausted check (or the shadow harness gets its own
        # sub-budget); today the default-off sample rate is the only guard.
        rate = self._settings.shadow_sample_rate
        if rate <= 0 or random.random() >= rate:
            return

        authoritative_runtime = self._settings.runtime
        shadow_runtime = "deep" if authoritative_runtime == "legacy" else "legacy"

        async def _resolve_cap(name: str) -> str | None:
            return await _resolve_capability(name, self._db_factory_provider(), workspace_id)

        shadow_executor = _IntentRecordingShadowExecutor(
            ShadowToolExecutor(self._tool_executor, _resolve_cap)
        )

        # Isolation covers the WHOLE operation: run_shadow_turn, the divergence
        # comparison, AND the metric emit. compare()/record are pure/cheap, but a
        # raise anywhere here would escape into the untracked background task, so the
        # try/except must wrap all of it to make the "never raises" invariant
        # structurally true — not merely true-by-luck-of-well-typed-inputs.
        try:
            shadow_decision = await self._invoker.run_shadow_turn(
                agent_name,
                message,
                user_id=user_id,
                workspace_id=workspace_id,
                runtime=shadow_runtime,
                tool_executor=shadow_executor,
            )
            for divergence in DivergenceComparator.compare(authoritative_decision, shadow_decision):
                MetricsService.record_shadow_divergence(kind=divergence.kind)
        except Exception:
            logger.warning(
                "[shadow] shadow run failed (isolated) — authoritative turn unaffected",
                exc_info=True,
            )
