"""Distributed tracing for Jarvis intelligence cycles.

Every orchestrator cycle gets a trace_id. Each sub-agent call within
that cycle gets a span. This provides full observability into the
agent chain: Observer -> Librarian -> Planner -> Governor -> Presenter.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ulid import ULID

logger = logging.getLogger(__name__)


@dataclass
class AgentSpan:
    span_id: str
    agent_name: str
    parent_span_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    tools_called: list[str] = field(default_factory=list)
    decision: str | None = None
    error: str | None = None

    def duration_ms(self) -> int:
        if self.started_at and self.ended_at:
            return int((self.ended_at - self.started_at).total_seconds() * 1000)
        return 0


@dataclass
class JarvisTrace:
    trace_id: str
    trigger: str  # user_message, perception_gmail, scheduled_briefing, etc.
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    spans: list[AgentSpan] = field(default_factory=list)
    _active_spans: dict[str, AgentSpan] = field(default_factory=dict, repr=False)

    def start_span(self, agent_name: str, parent_span_id: str | None = None) -> AgentSpan:
        span = AgentSpan(
            span_id=f"span_{ULID()}",
            agent_name=agent_name,
            parent_span_id=parent_span_id,
            started_at=datetime.now(timezone.utc),
        )
        self.spans.append(span)
        self._active_spans[span.span_id] = span
        return span

    def end_span(
        self,
        span_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tools_called: list[str] | None = None,
        decision: str | None = None,
        error: str | None = None,
    ) -> AgentSpan | None:
        span = self._active_spans.pop(span_id, None)
        if span is None:
            logger.warning("Attempted to end unknown span: %s", span_id)
            return None
        span.ended_at = datetime.now(timezone.utc)
        span.input_tokens = input_tokens
        span.output_tokens = output_tokens
        span.tools_called = tools_called or []
        span.decision = decision
        span.error = error
        return span

    def finish(self) -> None:
        self.ended_at = datetime.now(timezone.utc)
        # Close any still-active spans
        for span_id in list(self._active_spans.keys()):
            self.end_span(span_id, error="trace_finished_with_active_span")

    def total_tokens(self) -> tuple[int, int]:
        input_total = sum(s.input_tokens for s in self.spans)
        output_total = sum(s.output_tokens for s in self.spans)
        return input_total, output_total

    def duration_ms(self) -> int:
        if self.ended_at:
            return int((self.ended_at - self.started_at).total_seconds() * 1000)
        return int((datetime.now(timezone.utc) - self.started_at).total_seconds() * 1000)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms(),
            "total_input_tokens": self.total_tokens()[0],
            "total_output_tokens": self.total_tokens()[1],
            "spans": [
                {
                    "span_id": s.span_id,
                    "agent_name": s.agent_name,
                    "parent_span_id": s.parent_span_id,
                    "duration_ms": s.duration_ms(),
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "tools_called": s.tools_called,
                    "decision": s.decision,
                    "error": s.error,
                }
                for s in self.spans
            ],
        }


class TraceManager:
    """Manages active traces for the orchestrator."""

    def __init__(self, trace_store=None):
        self._active_traces: dict[str, JarvisTrace] = {}
        self._trace_store = trace_store

    def start_trace(self, trigger: str) -> JarvisTrace:
        trace = JarvisTrace(
            trace_id=f"trace_{ULID()}",
            trigger=trigger,
        )
        self._active_traces[trace.trace_id] = trace
        logger.info(
            "trace_started",
            extra={"trace_id": trace.trace_id, "trigger": trigger},
        )
        return trace

    async def finish_trace(self, trace_id: str) -> JarvisTrace | None:
        trace = self._active_traces.pop(trace_id, None)
        if trace:
            trace.finish()
            input_t, output_t = trace.total_tokens()
            logger.info(
                "trace_completed",
                extra={
                    "trace_id": trace.trace_id,
                    "trigger": trace.trigger,
                    "duration_ms": trace.duration_ms(),
                    "spans": len(trace.spans),
                    "input_tokens": input_t,
                    "output_tokens": output_t,
                },
            )
            # Persist to TraceStore for search and replay
            if self._trace_store:
                try:
                    await self._trace_store.store_trace(trace.to_dict())
                except Exception:
                    logger.warning("Failed to persist trace %s", trace_id, exc_info=True)
        return trace

    def get_trace(self, trace_id: str) -> JarvisTrace | None:
        return self._active_traces.get(trace_id)
