"""Prometheus-compatible metrics collection."""

import logging

from prometheus_client import Counter, Gauge, Histogram, generate_latest

logger = logging.getLogger(__name__)

# Counters
EVENTS_INGESTED = Counter(
    "jarvis_events_ingested_total",
    "Total events ingested",
    ["source", "event_type"],
)
PLANS_CREATED = Counter(
    "jarvis_plans_created_total",
    "Total plans created",
    ["decision"],
)
EXECUTIONS_COMPLETED = Counter(
    "jarvis_executions_completed_total",
    "Total executions completed",
    ["status"],
)
APPROVALS_DECIDED = Counter(
    "jarvis_approvals_decided_total",
    "Total approvals decided",
    ["decision"],
)
AGENT_CALLS = Counter(
    "jarvis_agent_calls_total",
    "Total agent calls",
    ["agent_name", "model"],
)

# Gauges
ACTIVE_RUNS = Gauge(
    "jarvis_active_runs",
    "Currently active task runs",
)
PENDING_APPROVALS = Gauge(
    "jarvis_pending_approvals",
    "Pending approvals",
)
BUDGET_REMAINING = Gauge(
    "jarvis_budget_remaining_usd",
    "Remaining daily budget in USD",
    ["user_id"],
)
ACTIVE_CONNECTORS = Gauge(
    "jarvis_active_connectors",
    "Active connectors",
    ["provider"],
)

# Histograms
EVENT_PROCESSING_LATENCY = Histogram(
    "jarvis_event_processing_seconds",
    "Event processing latency",
    ["source"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
AGENT_CALL_LATENCY = Histogram(
    "jarvis_agent_call_seconds",
    "Agent call latency",
    ["agent_name"],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)
EXECUTION_DURATION = Histogram(
    "jarvis_execution_duration_seconds",
    "Execution duration",
    buckets=[1, 5, 10, 30, 60, 120, 300],
)


class MetricsService:
    """Convenience wrapper for recording metrics."""

    @staticmethod
    def record_event_ingested(source: str, event_type: str) -> None:
        EVENTS_INGESTED.labels(source=source, event_type=event_type).inc()

    @staticmethod
    def record_plan_created(decision: str) -> None:
        PLANS_CREATED.labels(decision=decision).inc()

    @staticmethod
    def record_execution_completed(status: str) -> None:
        EXECUTIONS_COMPLETED.labels(status=status).inc()

    @staticmethod
    def record_approval_decided(decision: str) -> None:
        APPROVALS_DECIDED.labels(decision=decision).inc()

    @staticmethod
    def record_agent_call(agent_name: str, model: str, duration_ms: float) -> None:
        AGENT_CALLS.labels(agent_name=agent_name, model=model).inc()
        AGENT_CALL_LATENCY.labels(agent_name=agent_name).observe(duration_ms / 1000)

    @staticmethod
    def record_event_processing(source: str, duration_ms: float) -> None:
        EVENT_PROCESSING_LATENCY.labels(source=source).observe(duration_ms / 1000)

    @staticmethod
    def set_active_runs(count: int) -> None:
        ACTIVE_RUNS.set(count)

    @staticmethod
    def set_pending_approvals(count: int) -> None:
        PENDING_APPROVALS.set(count)

    @staticmethod
    def set_budget_remaining(user_id: str, amount: float) -> None:
        BUDGET_REMAINING.labels(user_id=user_id).set(amount)

    @staticmethod
    def generate_metrics() -> bytes:
        """Generate Prometheus-format metrics output."""
        return generate_latest()
