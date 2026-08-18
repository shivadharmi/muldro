"""Prometheus-compatible metrics collection."""

import logging

from prometheus_client import Counter, Gauge, Histogram, generate_latest

logger = logging.getLogger(__name__)

# Counters
EVENTS_INGESTED = Counter(
    "muldro_events_ingested_total",
    "Total events ingested",
    ["source", "event_type"],
)
PLANS_CREATED = Counter(
    "muldro_plans_created_total",
    "Total plans created",
    ["capability"],
)
EXECUTIONS_COMPLETED = Counter(
    "muldro_executions_completed_total",
    "Total executions completed",
    ["status"],
)
APPROVALS_DECIDED = Counter(
    "muldro_approvals_decided_total",
    "Total approvals decided",
    ["decision"],
)
AGENT_CALLS = Counter(
    "muldro_agent_calls_total",
    "Total agent calls",
    ["agent_name", "model"],
)
AGENT_RUNTIME_CALLS = Counter(
    "muldro_agent_runtime_calls_total",
    'Streaming agent calls on the deep Deep-Agents runtime (runtime label is always "deep")',
    ["runtime"],
)
TOOL_CALLS = Counter(
    "muldro_tool_calls_total",
    "Total tool calls",
    ["tool_name", "status"],
)
NOTIFICATIONS_SENT = Counter(
    "muldro_notifications_sent_total",
    "Total notifications sent",
    ["notification_type", "surface"],
)
TRIGGERS_FIRED = Counter(
    "muldro_triggers_fired_total",
    "Total triggers fired",
    ["action_type"],
)
MEMORY_WRITES = Counter(
    "muldro_memory_writes_total",
    "Total memory writes",
    ["memory_type"],
)

# Step-10 safety-invariant observability signals. double_fire is wired NOW at the
# idempotency wrapper (autonomous path only); the other three are dormant until
# their producers land (see the per-counter notes below).
DOUBLE_FIRE = Counter(
    "muldro_double_fire_total",
    "Idempotency double-fire detections (already-completed or in-flight re-fire blocked)",
    ["surface", "kind"],
)
# verification_false_negative: dormant until a real read_fn is wired in (10D) —
# the read_fn=None invariant is locked in Step 10A/A2.
VERIFICATION_FALSE_NEGATIVE = Counter(
    "muldro_verification_false_negative_total",
    "Verified writes later found to have not actually taken effect (false-negative on read-back)",
    ["surface"],
)
# double_prompt: dormant until the approval-creation observation hook lands (10C/10D).
DOUBLE_PROMPT = Counter(
    "muldro_double_prompt_total",
    "User re-prompted for approval of an action they had already authorized",
    ["surface"],
)
# ungated_perception_write: dormant until perception-provenance wiring lands (10C).
UNGATED_PERCEPTION_WRITE = Counter(
    "muldro_ungated_perception_write_total",
    "Perception-sourced write that executed without passing an approval gate",
    ["surface"],
)
# Gauges
ACTIVE_RUNS = Gauge(
    "muldro_active_runs",
    "Currently active task runs",
)
PENDING_APPROVALS = Gauge(
    "muldro_pending_approvals",
    "Pending approvals",
)
BUDGET_REMAINING = Gauge(
    "muldro_budget_remaining_usd",
    "Remaining daily budget in USD",
    ["user_id"],
)
ACTIVE_CONNECTORS = Gauge(
    "muldro_active_connectors",
    "Active connectors",
    ["provider"],
)
STUCK_RESUME_RUNS = Gauge(
    "muldro_stuck_resume_runs",
    "Runs approved by the user but stuck awaiting scheduler resume past the "
    "stale threshold (approval_resume recovery backlog)",
)

# Histograms
EVENT_PROCESSING_LATENCY = Histogram(
    "muldro_event_processing_seconds",
    "Event processing latency",
    ["source"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
AGENT_CALL_LATENCY = Histogram(
    "muldro_agent_call_seconds",
    "Agent call latency",
    ["agent_name"],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)
EXECUTION_DURATION = Histogram(
    "muldro_execution_duration_seconds",
    "Execution duration",
    buckets=[1, 5, 10, 30, 60, 120, 300],
)


class MetricsService:
    """Convenience wrapper for recording metrics."""

    @staticmethod
    def record_event_ingested(source: str, event_type: str) -> None:
        EVENTS_INGESTED.labels(source=source, event_type=event_type).inc()

    @staticmethod
    def record_plan_created(capability: str) -> None:
        PLANS_CREATED.labels(capability=capability).inc()

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
    def record_runtime_call(runtime: str) -> None:
        AGENT_RUNTIME_CALLS.labels(runtime=runtime).inc()

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
    def set_stuck_resume_runs(count: int) -> None:
        STUCK_RESUME_RUNS.set(count)

    @staticmethod
    def set_budget_remaining(user_id: str, amount: float) -> None:
        BUDGET_REMAINING.labels(user_id=user_id).set(amount)

    @staticmethod
    def record_tool_call(tool_name: str, status: str = "success") -> None:
        TOOL_CALLS.labels(tool_name=tool_name, status=status).inc()

    @staticmethod
    def record_notification_sent(notification_type: str, surface: str = "unknown") -> None:
        NOTIFICATIONS_SENT.labels(notification_type=notification_type, surface=surface).inc()

    @staticmethod
    def record_trigger_fired(action_type: str) -> None:
        TRIGGERS_FIRED.labels(action_type=action_type).inc()

    @staticmethod
    def record_memory_write(memory_type: str = "general") -> None:
        MEMORY_WRITES.labels(memory_type=memory_type).inc()

    @staticmethod
    def record_double_fire(surface: str, kind: str) -> None:
        DOUBLE_FIRE.labels(surface=surface, kind=kind).inc()

    @staticmethod
    def record_verification_false_negative(surface: str) -> None:
        VERIFICATION_FALSE_NEGATIVE.labels(surface=surface).inc()

    @staticmethod
    def record_double_prompt(surface: str) -> None:
        DOUBLE_PROMPT.labels(surface=surface).inc()

    @staticmethod
    def record_ungated_perception_write(surface: str) -> None:
        UNGATED_PERCEPTION_WRITE.labels(surface=surface).inc()

    @staticmethod
    def generate_metrics() -> bytes:
        """Generate Prometheus-format metrics output."""
        return generate_latest()
