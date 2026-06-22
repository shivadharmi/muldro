"""Distributed tracing for Jarvis intelligence cycles.

Every orchestrator cycle gets a trace_id. Each sub-agent call within
that cycle gets a span. This provides full observability into the
agent chain: Observer -> Librarian -> Planner -> Governor -> Presenter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ulid import ULID

from src.contracts import SpanRecord, SpanToolCall

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
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    thinking_tokens: int = 0
    model: str = "unknown"
    cost_usd: float = 0.0
    tools_called: list[str] = field(default_factory=list)
    tool_call_details: list[SpanToolCall] = field(default_factory=list)
    thinking_summary: str | None = None
    response_text: str | None = None
    decision: str | None = None
    error: str | None = None

    def duration_ms(self) -> int:
        if self.started_at and self.ended_at:
            return int((self.ended_at - self.started_at).total_seconds() * 1000)
        return 0

    def to_record(self) -> SpanRecord:
        """Convert to a Pydantic SpanRecord for persistence."""
        return SpanRecord(
            span_id=self.span_id,
            agent_name=self.agent_name,
            parent_span_id=self.parent_span_id,
            started_at=self.started_at,
            ended_at=self.ended_at,
            duration_ms=self.duration_ms(),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens,
            thinking_tokens=self.thinking_tokens,
            model=self.model,
            cost_usd=self.cost_usd,
            tools_called=self.tools_called,
            tool_call_details=self.tool_call_details,
            thinking_summary=self.thinking_summary,
            response_text=self.response_text,
            decision=self.decision,
            error=self.error,
        )


@dataclass
class JarvisTrace:
    trace_id: str
    trigger: str  # user_message, perception_gmail, scheduled_briefing, etc.
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    spans: list[AgentSpan] = field(default_factory=list)
    _active_spans: dict[str, AgentSpan] = field(default_factory=dict, repr=False)

    def start_span(
        self,
        agent_name: str,
        parent_span_id: str | None = None,
        *,
        model: str = "unknown",
    ) -> AgentSpan:
        span = AgentSpan(
            span_id=f"span_{ULID()}",
            agent_name=agent_name,
            parent_span_id=parent_span_id,
            started_at=datetime.now(timezone.utc),
            model=model,
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
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        thinking_tokens: int = 0,
        model: str | None = None,
        cost_usd: float = 0.0,
        tools_called: list[str] | None = None,
        tool_call_details: list[SpanToolCall] | None = None,
        thinking_summary: str | None = None,
        response_text: str | None = None,
        error: str | None = None,
    ) -> AgentSpan | None:
        span = self._active_spans.pop(span_id, None)
        if span is None:
            logger.warning("Attempted to end unknown span: %s", span_id)
            return None
        span.ended_at = datetime.now(timezone.utc)
        span.input_tokens = input_tokens
        span.output_tokens = output_tokens
        span.cache_creation_input_tokens = cache_creation_input_tokens
        span.cache_read_input_tokens = cache_read_input_tokens
        span.thinking_tokens = thinking_tokens
        # Preserve the model set at start_span when the caller doesn't supply one
        # (e.g. finish() force-closing an abandoned span), so a real model id is
        # not clobbered back to the "unknown" sentinel.
        if model is not None:
            span.model = model
        span.cost_usd = cost_usd
        span.tools_called = tools_called or []
        span.tool_call_details = tool_call_details or []
        span.thinking_summary = thinking_summary
        span.response_text = response_text
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
            "spans": [s.to_record().model_dump(mode="json") for s in self.spans],
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

    async def finish_trace(
        self, trace_id: str, *, user_id: str, workspace_id: str
    ) -> JarvisTrace | None:
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
                    await self._trace_store.store_trace(
                        trace.to_dict(), user_id=user_id, workspace_id=workspace_id
                    )
                except Exception:
                    logger.warning("Failed to persist trace %s", trace_id, exc_info=True)
        return trace

    def get_trace(self, trace_id: str) -> JarvisTrace | None:
        return self._active_traces.get(trace_id)
