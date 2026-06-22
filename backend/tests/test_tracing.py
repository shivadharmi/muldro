"""Tests for AgentSpan model propagation through the trace lifecycle.

A span that performed an API call must carry its real model id into cost
calculation. Force-closing an abandoned span (via finish()) must not clobber
that real model back to the "unknown" sentinel.
"""

from src.orchestrator.tracing import JarvisTrace


class TestSpanModelPropagation:
    def _trace(self) -> JarvisTrace:
        return JarvisTrace(trace_id="trace_test", trigger="user_message")

    def test_start_span_carries_model(self):
        trace = self._trace()
        span = trace.start_span("planner", model="claude-opus-4-8")
        assert span.model == "claude-opus-4-8"

    def test_end_span_sets_real_model(self):
        trace = self._trace()
        span = trace.start_span("planner", model="claude-opus-4-8")
        ended = trace.end_span(
            span.span_id,
            input_tokens=1000,
            output_tokens=500,
            model="claude-opus-4-8",
        )
        assert ended is not None
        assert ended.model == "claude-opus-4-8"

    def test_finish_force_close_preserves_started_model(self):
        """finish() closes still-active spans with only error=...; the real
        model recorded at start_span must survive."""
        trace = self._trace()
        span = trace.start_span("planner", model="claude-opus-4-8")
        trace.finish()
        assert span.error == "trace_finished_with_active_span"
        # Model must NOT be reset to the "unknown" sentinel.
        assert span.model == "claude-opus-4-8"

    def test_default_model_is_unknown_when_not_supplied(self):
        trace = self._trace()
        span = trace.start_span("perceiver")
        assert span.model == "unknown"
        trace.finish()
        assert span.model == "unknown"
