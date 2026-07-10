"""One-directional auto-rollback watcher (Step 10B Phase 5, Task 5a).

Watches the Step 10B cutover-control-plane rollback-gate signals
(``src/services/metrics_service.py``) for surfaces CURRENTLY resolving to
``"deep"`` and trips the surface's breaker back to ``"legacy"`` the moment any
mapped signal's per-TICK DELTA breaches its configured threshold
(``settings.rollback_*_threshold``). ``"legacy"`` is the proven-safe runtime,
so the trip direction is fixed: this tick may ONLY call ``runtime_breaker.trip``.
It must NEVER write an enable key (=deep) and NEVER call ``runtime_breaker.clear``
— re-enabling ``"deep"`` for a surface is a deliberate MANUAL human action
(anti-flap by design; see the Task 5b escape hatch in ``runtime_breaker.py`` /
``routes_admin_runtime.py``).

Byte-neutral gate: while every surface resolves to ``"legacy"`` (the 10B
default — no enable key set anywhere), ``effective_runtime(surface) != "deep"``
for every surface on every tick, so the per-surface signal checks never run —
this tick is a COMPLETE no-op on the live path.

Signal -> surface map (resolved design):
  * ``double_fire``                 -> "autonomous" only (summed across its two
    kinds: already_done, in_flight_conflict).
  * ``verification_false_negative`` -> the currently-deep surface being evaluated
    (the counter is itself labeled by surface).
  * ``double_prompt``               -> "chat" only.
  * ``ungated_perception_write``    -> "perception" AND "autonomous".
  * ``shadow_divergence``           -> EVERY currently-deep surface (the counter
    is labeled by ``kind``, not surface, so a breach is treated as applying to
    every surface currently being watched; summed across all known kinds).

Anti-churn WITHOUT an explicit cooldown timer: the moment a surface trips,
``effective_runtime(surface)`` resolves it to ``"legacy"`` on the very next
read (the breaker tier outranks the enable tier — see ``runtime_gate.py``), so
the next tick's ``if rt != "deep": continue`` guard skips it entirely. A
just-tripped surface is never re-evaluated until a human clears the breaker or
re-sets the enable key. No ``opened_at``/cooldown bookkeeping is needed for
10B. (Known 10D-hardening gap: per-signal baselines are not reset on a manual
re-enable, so the first post-re-enable tick's delta includes any signal
activity accrued during the tripped interval — acceptable for 10B since a
manual re-enable is itself a deliberate, watched human action.)

Cold start: the FIRST observation of any (signal, surface) pair only
establishes the baseline — it never trips, so an already-elevated cumulative
counter at process start can never look like a sudden breach.

Process-local-counter limitation: ``prometheus_client`` Counters are
process-LOCAL. This watcher reads deltas from THIS process's in-process
registry (``MetricsService.read_counter_total``) — in a multi-process deploy
it only observes signal activity from ITS OWN process. Acceptable for 10B:
the same counters are ALSO scraped by Prometheus externally, and a prod
watcher that aggregates across processes via the Prometheus HTTP API is
10D/ops hardening, not this tick's job.
"""

from __future__ import annotations

import logging

from src.services import runtime_breaker
from src.services.metrics_service import (
    DOUBLE_FIRE,
    DOUBLE_PROMPT,
    SHADOW_DIVERGENCE,
    UNGATED_PERCEPTION_WRITE,
    VERIFICATION_FALSE_NEGATIVE,
    MetricsService,
)
from src.services.runtime_gate import effective_runtime

logger = logging.getLogger(__name__)

# double_fire kinds summed for surface="autonomous" (src/services/idempotency/wrapper.py).
_DOUBLE_FIRE_KINDS = ("already_done", "in_flight_conflict")
# shadow_divergence kinds summed (src/orchestrator/divergence.py's Divergence.kind).
_SHADOW_DIVERGENCE_KINDS = (
    "route",
    "write_intent_set",
    "final_text",
    "gate_verdict",
    "read_synthesis",
)

# signal name -> the settings field holding its breach threshold.
_THRESHOLD_FIELD = {
    "double_fire": "rollback_double_fire_threshold",
    "verification_false_negative": "rollback_verification_false_negative_threshold",
    "double_prompt": "rollback_double_prompt_threshold",
    "ungated_perception_write": "rollback_ungated_perception_write_threshold",
    "shadow_divergence": "rollback_shadow_divergence_threshold",
}


class RuntimeRollbackTickMixin:
    """Periodic one-directional auto-rollback watcher: breach -> trip to legacy."""

    async def _tick_runtime_rollback(self, factory) -> None:
        """One tick of the auto-rollback watcher. See module docstring for the
        full signal->surface map, the anti-churn mechanism, and the
        process-local-counter caveat. ``factory`` is accepted (unused) only to
        match the uniform ``_tick_xxx(self, factory)`` signature every other
        scheduler tick mixin uses at the ``_run_subtick`` call site.
        """
        redis = self._rollback_redis()
        if redis is None:
            return

        if not hasattr(self, "_rollback_last_seen"):
            self._rollback_last_seen: dict[tuple[str, str], float] = {}

        for surface in runtime_breaker.VALID_SURFACES:
            rt = await effective_runtime(surface, redis=redis, settings=self._settings)
            if rt != "deep":
                continue  # only watch surfaces CURRENTLY deep (the byte-neutral gate)

            for signal, total in self._signals_for_surface(surface):
                await self._check_and_trip(redis, surface, signal, total)

    def _rollback_redis(self):
        """Resolve the shared Redis client via the orchestrator's service
        container (mirrors ``perception_tick.py`` / ``agent_invoker.py``'s
        ``services.extras.get("redis")`` pattern). Returns ``None`` (no-op tick)
        when no orchestrator, no services container, or no Redis is reachable in
        this process."""
        orchestrator = getattr(self, "_orchestrator", None)
        services = getattr(orchestrator, "_services", None) if orchestrator else None
        if services is None:
            return None
        extras = getattr(services, "extras", None)
        if not isinstance(extras, dict):
            return None
        return extras.get("redis")

    @staticmethod
    def _signals_for_surface(surface: str) -> list[tuple[str, float]]:
        """(signal_name, current cumulative total) pairs mapped to ``surface`` per
        the resolved design (see module docstring)."""
        signals: list[tuple[str, float]] = [
            (
                "verification_false_negative",
                MetricsService.read_counter_total(VERIFICATION_FALSE_NEGATIVE, surface=surface),
            ),
            (
                "shadow_divergence",
                sum(
                    MetricsService.read_counter_total(SHADOW_DIVERGENCE, kind=k)
                    for k in _SHADOW_DIVERGENCE_KINDS
                ),
            ),
        ]
        if surface == "autonomous":
            signals.append(
                (
                    "double_fire",
                    sum(
                        MetricsService.read_counter_total(DOUBLE_FIRE, surface="autonomous", kind=k)
                        for k in _DOUBLE_FIRE_KINDS
                    ),
                )
            )
        if surface == "chat":
            signals.append(
                (
                    "double_prompt",
                    MetricsService.read_counter_total(DOUBLE_PROMPT, surface=surface),
                )
            )
        if surface in ("perception", "autonomous"):
            signals.append(
                (
                    "ungated_perception_write",
                    MetricsService.read_counter_total(UNGATED_PERCEPTION_WRITE, surface=surface),
                )
            )
        return signals

    async def _check_and_trip(self, redis, surface: str, signal: str, total: float) -> None:
        """Compute the delta since the last observation of ``(signal, surface)``
        and trip the breaker when it breaches the configured threshold.

        The FIRST-EVER observation of a key only records the baseline (cold
        start never trips). The baseline is updated to ``total`` on every call,
        breach or not, so the next tick always measures a fresh delta.
        """
        key = (signal, surface)
        baseline = self._rollback_last_seen.get(key)
        self._rollback_last_seen[key] = total
        if baseline is None:
            return

        delta = total - baseline
        if delta <= 0:
            return

        threshold = getattr(self._settings, _THRESHOLD_FIELD[signal])
        if delta >= threshold:
            await runtime_breaker.trip(redis, surface)
            logger.warning(
                "runtime rollback watcher: surface=%s signal=%s delta=%.0f "
                "threshold=%.0f -- TRIPPED to legacy",
                surface,
                signal,
                delta,
                threshold,
            )
