"""AlertingService — SLO monitoring and alerting.

Checks service-level objectives (event latency, error rates, budget)
and sends alerts via the Notifier when thresholds are breached.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class SLOCheck:
    name: str
    status: str  # ok, warning, critical
    value: float
    threshold: float
    message: str
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AlertingService:
    """Monitors SLOs and fires alerts on breach."""

    def __init__(self, notifier=None, trace_store=None, budget_tracker=None):
        self._notifier = notifier
        self._trace_store = trace_store
        self._budget = budget_tracker
        self._last_alerts: dict[str, datetime] = {}
        self._cooldown_seconds = 300  # 5 minute cooldown between same alerts

    async def check_all_slos(self) -> list[SLOCheck]:
        """Run all SLO checks and return results."""
        checks = []
        checks.append(await self._check_event_latency())
        checks.append(await self._check_error_rate())
        checks.append(await self._check_budget())

        for check in checks:
            if check.status in ("warning", "critical"):
                await self._fire_alert(check)

        return checks

    async def _check_event_latency(self) -> SLOCheck:
        """SLO: median event processing latency < 2000ms."""
        threshold = 2000.0
        if not self._trace_store:
            return SLOCheck(
                name="event_latency",
                status="ok",
                value=0,
                threshold=threshold,
                message="No trace store configured",
            )

        perf = await self._trace_store.get_agent_performance(time_range_hours=1)
        observer = perf.get("observer", {})
        avg_ms = observer.get("avg_duration_ms", 0)

        status = "ok"
        if avg_ms > threshold:
            status = "critical"
        elif avg_ms > threshold * 0.8:
            status = "warning"

        return SLOCheck(
            name="event_latency",
            status=status,
            value=avg_ms,
            threshold=threshold,
            message=f"Observer avg latency: {avg_ms}ms",
        )

    async def _check_error_rate(self) -> SLOCheck:
        """SLO: agent error rate < 5%."""
        threshold = 5.0
        if not self._trace_store:
            return SLOCheck(
                name="error_rate",
                status="ok",
                value=0,
                threshold=threshold,
                message="No trace store configured",
            )

        perf = await self._trace_store.get_agent_performance(time_range_hours=1)
        total_calls = sum(a.get("call_count", 0) for a in perf.values())
        total_errors = sum(a.get("error_count", 0) for a in perf.values())

        if total_calls == 0:
            return SLOCheck(
                name="error_rate",
                status="ok",
                value=0,
                threshold=threshold,
                message="No agent calls in window",
            )

        rate = (total_errors / total_calls) * 100
        status = "ok"
        if rate > threshold:
            status = "critical"
        elif rate > threshold * 0.6:
            status = "warning"

        return SLOCheck(
            name="error_rate",
            status=status,
            value=round(rate, 1),
            threshold=threshold,
            message=f"Error rate: {rate:.1f}% ({total_errors}/{total_calls})",
        )

    async def _check_budget(self) -> SLOCheck:
        """SLO: daily budget usage < 90%."""
        threshold = 90.0
        if not self._budget:
            return SLOCheck(
                name="budget_usage",
                status="ok",
                value=0,
                threshold=threshold,
                message="No budget tracker configured",
            )

        try:
            snapshot = self._budget.snapshot()
            used = snapshot.get("daily_spend_usd", 0)
            limit = snapshot.get("daily_limit_usd", 5.0)
            pct = (used / limit * 100) if limit > 0 else 0
        except Exception:
            pct = 0

        status = "ok"
        if pct > threshold:
            status = "critical"
        elif pct > threshold * 0.8:
            status = "warning"

        return SLOCheck(
            name="budget_usage",
            status=status,
            value=round(pct, 1),
            threshold=threshold,
            message=f"Budget usage: {pct:.1f}%",
        )

    async def _fire_alert(self, check: SLOCheck) -> None:
        """Send alert if not in cooldown."""
        now = datetime.now(timezone.utc)
        last = self._last_alerts.get(check.name)
        if last and (now - last).total_seconds() < self._cooldown_seconds:
            return

        self._last_alerts[check.name] = now
        logger.warning("SLO alert: %s [%s] %s", check.name, check.status, check.message)

        if self._notifier:
            try:
                await self._notifier.send(
                    user_id="system",
                    title=f"SLO Alert: {check.name}",
                    body=check.message,
                    notification_type="slo_alert",
                    data={
                        "slo": check.name,
                        "status": check.status,
                        "value": check.value,
                        "threshold": check.threshold,
                    },
                )
            except Exception:
                logger.exception("Failed to send SLO alert notification")
