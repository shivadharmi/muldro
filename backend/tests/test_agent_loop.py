"""Tests for the unified agent loop (agent_loop.py).

Covers: basic flow, tool execution, timeouts, retries, error signaling,
thinking fallback, governor blocking, circuit breaker, and token tracking.
"""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest


class TestThinkingParamsModelAware:
    """build_thinking_params must speak each model's dialect.

    Opus 4.7/4.8 and Fable/Mythos 5 reject `temperature` and
    `thinking:{type:enabled}` — they require adaptive thinking +
    output_config.effort. Legacy models keep the enabled+budget+temperature
    surface. Regression for the live Planner 400 ("temperature is deprecated",
    "thinking.type.enabled is not supported")."""

    def test_opus_4_8_adaptive_no_temperature(self):
        from src.orchestrator.agent_loop import build_thinking_params

        params = build_thinking_params(
            "claude-opus-4-8", thinking_enabled=True, budget_tokens=8192, temperature=0.3
        )
        assert params["thinking"]["type"] == "adaptive"
        assert "temperature" not in params
        assert params["output_config"]["effort"] == "high"

    def test_opus_4_8_disabled_sends_no_temperature(self):
        from src.orchestrator.agent_loop import build_thinking_params

        params = build_thinking_params(
            "claude-opus-4-8", thinking_enabled=False, budget_tokens=None, temperature=0.3
        )
        # Sampling params 400 on these models — must not appear, even disabled.
        assert "temperature" not in params
        assert "thinking" not in params

    def test_bedrock_opus_4_8_profile_is_adaptive(self):
        from src.orchestrator.agent_loop import build_thinking_params

        params = build_thinking_params(
            "us.anthropic.claude-opus-4-8", thinking_enabled=True, budget_tokens=2048, temperature=1
        )
        assert params["thinking"]["type"] == "adaptive"
        assert "temperature" not in params
        assert params["output_config"]["effort"] == "low"  # small budget → low effort

    def test_legacy_sonnet_keeps_enabled_thinking_and_temperature(self):
        from src.orchestrator.agent_loop import build_thinking_params

        params = build_thinking_params(
            "claude-sonnet-4-6", thinking_enabled=True, budget_tokens=4096, temperature=0.3
        )
        assert params["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        assert params["temperature"] == 1  # required when enabled-thinking is on

    def test_legacy_sonnet_disabled_uses_agent_temperature(self):
        from src.orchestrator.agent_loop import build_thinking_params

        params = build_thinking_params(
            "claude-sonnet-4-6", thinking_enabled=False, budget_tokens=None, temperature=0.3
        )
        assert params == {"temperature": 0.3}


# ── Helpers ─────────────────────────────────────────────────────────────


@dataclass
class FakeThinkingConfig:
    enabled: bool = False
    budget_tokens: int = 4096


@dataclass
class FakeSubAgent:
    name: str = "test_agent"
    model_tier: str = "sonnet"
    # Non-empty by default so capability-scope enforcement (FIX #1) does not
    # block tools in tests that exercise other behaviors. Tests that assert
    # scope rejection pass an explicit scope + patch ToolRegistry.
    capability_scope: set = field(default_factory=lambda: {"test.cap"})
    max_tokens: int = 4096
    temperature: float = 0.3
    thinking: FakeThinkingConfig = field(default_factory=FakeThinkingConfig)

    def can_use_tool(self, tool_name: str) -> bool:
        return True


class FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeToolUseBlock:
    def __init__(self, tool_id: str, name: str, input_data: dict):
        self.type = "tool_use"
        self.id = tool_id
        self.name = name
        self.input = input_data

    def model_dump(self, **kwargs):
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


class FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=20):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0


class FakeResponse:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


def make_text_response(text: str) -> FakeResponse:
    return FakeResponse([FakeTextBlock(text)])


def make_tool_response(tool_name: str, tool_input: dict, tool_id: str = "tool_1") -> FakeResponse:
    return FakeResponse([FakeToolUseBlock(tool_id, tool_name, tool_input)])


def make_multi_tool_response(*tools: tuple[str, dict]) -> FakeResponse:
    """Build a single assistant response containing multiple tool_use blocks."""
    return FakeResponse(
        [
            FakeToolUseBlock(f"tool_{i}", name, tool_input)
            for i, (name, tool_input) in enumerate(tools)
        ]
    )


def _make_budget():
    budget = MagicMock()
    budget.record_usage = AsyncMock(return_value=MagicMock(cost_usd=0.001))
    return budget


def _make_db_factory():
    db = AsyncMock()
    db.commit = AsyncMock()

    class FakeFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            pass

    return FakeFactory()


async def _collect_events(gen):
    """Collect all events from an async generator."""
    events = []
    async for evt in gen:
        events.append(evt)
    return events


# ── Tests ───────────────────────────────────────────────────────────────


class TestAgentLoop:
    @pytest.fixture(autouse=True)
    def _default_in_scope_registry(self):
        """By default, resolve tools to the FakeSubAgent's default capability
        ("test.cap") so capability-scope enforcement permits them. Tests that
        exercise scope rejection wrap their own patch("...ToolRegistry"), which
        takes precedence inside their context."""
        from unittest.mock import patch as _patch

        fake_tool = MagicMock()
        fake_tool.capability = "test.cap"
        registry = MagicMock()
        registry.get_tool = AsyncMock(return_value=fake_tool)
        with _patch("src.orchestrator.agent_loop.ToolRegistry", return_value=registry):
            yield

    @pytest.fixture
    def client(self):
        c = AsyncMock()
        return c

    @pytest.fixture
    def agent(self):
        return FakeSubAgent()

    @pytest.fixture
    def trace(self):
        t = MagicMock()
        t.trace_id = "trace_test"
        t.trigger = "test"
        span = MagicMock()
        span.span_id = "span_test"
        t.start_span.return_value = span
        return t

    async def test_basic_text_response(self, client, agent, trace):
        """Agent returns text → yields LoopAgentStart, LoopDone."""
        from src.orchestrator.agent_loop import LoopAgentStart, LoopDone, agent_loop

        client.messages.create = AsyncMock(return_value=make_text_response("Hello!"))

        events = await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[],
                message="Hi",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(),
            )
        )

        assert any(isinstance(e, LoopAgentStart) for e in events)
        done_events = [e for e in events if isinstance(e, LoopDone)]
        assert len(done_events) == 1
        assert done_events[0].text == "Hello!"

    async def test_tool_call_and_result(self, client, agent, trace):
        """Tool is called → result returned → loop continues to text response."""
        from src.orchestrator.agent_loop import LoopDone, LoopToolCall, LoopToolResult, agent_loop

        # First call returns tool use, second returns text
        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("search_memory", {"query": "test"}),
                make_text_response("Found it!"),
            ]
        )

        tool_fn = AsyncMock(return_value={"results": ["item1"]})
        events = await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "search_memory", "description": "Search", "input_schema": {}}],
                message="Search for test",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=tool_fn,
            )
        )

        tool_calls = [e for e in events if isinstance(e, LoopToolCall)]
        tool_results = [e for e in events if isinstance(e, LoopToolResult)]
        done = [e for e in events if isinstance(e, LoopDone)]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "search_memory"
        assert len(tool_results) == 1
        assert len(done) == 1
        assert done[0].text == "Found it!"

    async def test_successful_tool_call_records_metric(self, client, agent, trace):
        """A successful tool call increments the tool-call metric (success)."""
        from unittest.mock import patch

        from src.orchestrator.agent_loop import agent_loop

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("search_memory", {"query": "test"}),
                make_text_response("done"),
            ]
        )
        tool_fn = AsyncMock(return_value={"results": ["x"]})

        with patch("src.services.metrics_service.MetricsService") as mock_metrics:
            await _collect_events(
                agent_loop(
                    client=client,
                    agent=agent,
                    model="claude-sonnet-4-20250514",
                    system_blocks=[],
                    tools=[{"name": "search_memory", "description": "S", "input_schema": {}}],
                    message="go",
                    user_id="usr_test",
                    workspace_id="ws_test",
                    db_factory=_make_db_factory(),
                    services=MagicMock(),
                    budget=_make_budget(),
                    trace=trace,
                    execute_tool_fn=tool_fn,
                )
            )

        mock_metrics.record_tool_call.assert_any_call("search_memory", status="success")

    async def test_errored_tool_call_records_error_metric(self, client, agent, trace):
        """A tool that returns an error increments the tool-call metric (error)."""
        from unittest.mock import patch

        from src.orchestrator.agent_loop import agent_loop

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("search_memory", {"query": "test"}),
                make_text_response("done"),
            ]
        )
        tool_fn = AsyncMock(return_value={"error": "boom"})

        with patch("src.services.metrics_service.MetricsService") as mock_metrics:
            await _collect_events(
                agent_loop(
                    client=client,
                    agent=agent,
                    model="claude-sonnet-4-20250514",
                    system_blocks=[],
                    tools=[{"name": "search_memory", "description": "S", "input_schema": {}}],
                    message="go",
                    user_id="usr_test",
                    workspace_id="ws_test",
                    db_factory=_make_db_factory(),
                    services=MagicMock(),
                    budget=_make_budget(),
                    trace=trace,
                    execute_tool_fn=tool_fn,
                )
            )

        mock_metrics.record_tool_call.assert_any_call("search_memory", status="error")

    async def test_agent_call_records_metric(self, client, agent, trace):
        """Each API roundtrip records an agent-call metric (name, model, duration)."""
        from unittest.mock import patch

        from src.orchestrator.agent_loop import agent_loop

        client.messages.create = AsyncMock(return_value=make_text_response("Hello!"))

        with patch("src.services.metrics_service.MetricsService") as mock_metrics:
            await _collect_events(
                agent_loop(
                    client=client,
                    agent=agent,
                    model="claude-sonnet-4-20250514",
                    system_blocks=[],
                    tools=[],
                    message="hi",
                    user_id="usr_test",
                    workspace_id="ws_test",
                    db_factory=_make_db_factory(),
                    services=MagicMock(),
                    budget=_make_budget(),
                    trace=trace,
                    execute_tool_fn=AsyncMock(),
                )
            )

        mock_metrics.record_agent_call.assert_called()
        args = mock_metrics.record_agent_call.call_args.args
        assert args[1] == "claude-sonnet-4-20250514"  # model
        assert args[2] >= 0  # duration_ms

    async def test_tool_timeout_60s(self, client, agent, trace):
        """Tool exceeding 60s timeout returns timed_out error."""
        from src.orchestrator.agent_loop import LoopToolResult, agent_loop

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("slow_tool", {}),
                make_text_response("Ok"),
            ]
        )

        async def slow_tool(*args, **kwargs):
            await asyncio.sleep(100)

        events = await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "slow_tool", "description": "Slow", "input_schema": {}}],
                message="Do slow thing",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=slow_tool,
            )
        )

        tool_results = [e for e in events if isinstance(e, LoopToolResult)]
        assert len(tool_results) == 1
        assert isinstance(tool_results[0].result, dict)
        assert tool_results[0].result.get("timed_out") is True

    async def test_api_retry_on_rate_limit(self, client, agent, trace):
        """RateLimitError triggers retries with backoff."""
        from src.orchestrator.agent_loop import LoopDone, agent_loop

        rate_err = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}, json=MagicMock(return_value={})),
            body={},
        )
        client.messages.create = AsyncMock(side_effect=[rate_err, make_text_response("Recovered")])

        events = await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[],
                message="Hi",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(),
            )
        )

        done = [e for e in events if isinstance(e, LoopDone)]
        assert len(done) == 1
        assert done[0].text == "Recovered"

    async def test_error_signaling_is_error_flag(self, client, agent, trace):
        """Tool returning {"error": ...} gets is_error flag in tool result."""
        from src.orchestrator.agent_loop import agent_loop

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("failing_tool", {}),
                make_text_response("Handled error"),
            ]
        )

        tool_fn = AsyncMock(return_value={"error": "connection refused"})
        await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "failing_tool", "description": "Fails", "input_schema": {}}],
                message="Try it",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=tool_fn,
            )
        )

        # The tool result sent to Claude should have is_error flag
        assert client.messages.create.call_count == 2
        second_call_messages = client.messages.create.call_args_list[1][1]["messages"]
        tool_result_msg = second_call_messages[-1]  # last message is tool result
        assert tool_result_msg["role"] == "user"
        result_block = tool_result_msg["content"][0]
        assert result_block.get("is_error") is True

    async def test_error_signaling_ok_status_not_flagged(self, client, agent, trace):
        """Tool returning {"error": ..., "status": "ok"} is NOT flagged as error."""
        from src.orchestrator.agent_loop import agent_loop

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("soft_error_tool", {}),
                make_text_response("Ok"),
            ]
        )

        tool_fn = AsyncMock(return_value={"error": "minor warning", "status": "ok"})
        await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "soft_error_tool", "description": "Soft", "input_schema": {}}],
                message="Try",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=tool_fn,
            )
        )

        second_call_messages = client.messages.create.call_args_list[1][1]["messages"]
        result_block = second_call_messages[-1]["content"][0]
        assert "is_error" not in result_block

    async def test_governor_blocks_tool(self, client, agent, trace):
        """Governor blocking tool → LoopToolResult(blocked=True)."""
        from src.orchestrator.agent_loop import LoopToolResult, agent_loop

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("gmail_send_email", {"to": "x@y.com"}),
                make_text_response("Blocked"),
            ]
        )

        with patch("src.orchestrator.agent_loop.governor_pre_tool_hook") as mock_hook:
            mock_hook.return_value = {
                "allowed": False,
                "approval_required": True,
                "approval_id": "apr_test",
                "reason": "Needs approval",
            }

            events = await _collect_events(
                agent_loop(
                    client=client,
                    agent=agent,
                    model="claude-sonnet-4-20250514",
                    system_blocks=[],
                    tools=[{"name": "gmail_send_email", "description": "Send", "input_schema": {}}],
                    message="Send email",
                    user_id="usr_test",
                    workspace_id="ws_test",
                    db_factory=_make_db_factory(),
                    services=MagicMock(),
                    budget=_make_budget(),
                    trace=trace,
                    execute_tool_fn=AsyncMock(),
                )
            )

        blocked_results = [e for e in events if isinstance(e, LoopToolResult) and e.blocked]
        assert len(blocked_results) == 1

    async def test_max_tool_rounds_limit(self, client, agent, trace):
        """Hitting max tool rounds → LoopDone with warning text."""
        from src.orchestrator.agent_loop import LoopDone, agent_loop

        # Always return tool calls, never text
        client.messages.create = AsyncMock(return_value=make_tool_response("infinite_tool", {}))

        events = await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "infinite_tool", "description": "Loop", "input_schema": {}}],
                message="Loop forever",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(return_value={"ok": True}),
                max_tool_rounds=3,
            )
        )

        done = [e for e in events if isinstance(e, LoopDone)]
        assert len(done) == 1
        assert "max tool rounds" in done[0].text.lower()

    async def test_token_usage_recorded(self, client, agent, trace):
        """Budget.record_usage is called with correct model and tokens."""
        from src.orchestrator.agent_loop import agent_loop

        client.messages.create = AsyncMock(return_value=make_text_response("Done"))
        budget = _make_budget()

        await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[],
                message="Hi",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=budget,
                trace=trace,
                execute_tool_fn=AsyncMock(),
            )
        )

        budget.record_usage.assert_called_once()
        call_kwargs = budget.record_usage.call_args[1]
        assert call_kwargs["agent_name"] == "test_agent"
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["input_tokens"] == 10
        assert call_kwargs["output_tokens"] == 20

    async def test_circuit_breaker_open_skips_api(self, client, agent, trace):
        """Circuit breaker OPEN → LoopError without API call."""
        from src.orchestrator.agent_loop import LoopError, agent_loop
        from src.orchestrator.api_circuit_breaker import AnthropicCircuitBreaker

        breaker = AnthropicCircuitBreaker(failure_threshold=1)
        breaker.record_failure("claude-sonnet-4-20250514")  # open circuit

        events = await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[],
                message="Hi",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(),
                circuit_breaker=breaker,
            )
        )

        errors = [e for e in events if isinstance(e, LoopError)]
        assert len(errors) == 1
        assert "circuit open" in errors[0].message.lower()
        # API should NOT have been called
        client.messages.create.assert_not_called()

    async def test_circuit_breaker_records_on_api_error(self, client, agent, trace):
        """API error records failure on circuit breaker."""
        from src.orchestrator.agent_loop import agent_loop
        from src.orchestrator.api_circuit_breaker import AnthropicCircuitBreaker

        breaker = AnthropicCircuitBreaker(failure_threshold=5)
        api_err = anthropic.APIStatusError(
            message="server error",
            response=MagicMock(status_code=500, headers={}, json=MagicMock(return_value={})),
            body={},
        )
        client.messages.create = AsyncMock(side_effect=api_err)

        await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[],
                message="Hi",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(),
                circuit_breaker=breaker,
            )
        )

        assert breaker._circuits["claude-sonnet-4-20250514"].failure_count == 1

    async def test_circuit_breaker_half_open_probe_reset_on_abandon(self):
        """Half-open probe lock is released even if generator is abandoned."""
        from src.orchestrator.api_circuit_breaker import AnthropicCircuitBreaker, CircuitState

        breaker = AnthropicCircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        model = "claude-sonnet-4-20250514"

        # Drive circuit to OPEN then let cooldown expire (0s) → HALF_OPEN
        breaker.record_failure(model)
        assert breaker.get_state(model) == CircuitState.HALF_OPEN

        # Simulate probe: is_available sets _half_open_testing = True
        assert breaker.is_available(model) is True
        assert breaker._half_open_testing[model] is True

        # Second call should be blocked (already probing)
        assert breaker.is_available(model) is False

        # Simulate abandoned probe — reset_half_open_probe clears the lock
        breaker.reset_half_open_probe(model)
        assert breaker._half_open_testing[model] is False

        # Now a new probe should be allowed
        assert breaker.is_available(model) is True

    async def test_tool_token_usage_records_input_output(self, client, agent, trace):
        """Per-tool TokenUsage records should have non-zero input/output tokens."""
        from src.orchestrator.agent_loop import agent_loop

        tool_response = make_tool_response("search_memory", {"query": "test"})
        tool_response.usage = FakeUsage(input_tokens=100, output_tokens=40)
        tool_response.usage.cache_creation_input_tokens = 5
        tool_response.usage.cache_read_input_tokens = 10

        client.messages.create = AsyncMock(side_effect=[tool_response, make_text_response("Done")])

        added_records = []

        class CapturingDB:
            def add(self, obj):
                added_records.append(obj)

            async def commit(self):
                pass

        class CapturingFactory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return CapturingDB()

            async def __aexit__(self, *args):
                pass

        await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "search_memory", "description": "Search", "input_schema": {}}],
                message="Search test",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=CapturingFactory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(return_value={"results": []}),
            )
        )

        from src.models.token_usage import TokenUsage

        tool_usages = [r for r in added_records if isinstance(r, TokenUsage)]
        assert len(tool_usages) == 1, f"Expected 1 tool TokenUsage, got {len(tool_usages)}"
        record = tool_usages[0]
        assert record.input_tokens == 100, f"Expected 100 input_tokens, got {record.input_tokens}"
        assert record.output_tokens == 40, f"Expected 40 output_tokens, got {record.output_tokens}"
        assert record.cache_creation_input_tokens == 5
        assert record.cache_read_input_tokens == 10
        assert record.trigger == "tool:search_memory"

    async def test_multi_tool_round_writes_per_tool_breakdown_rows(self, client, agent, trace):
        """A round with N tool calls writes N per-tool TokenUsage rows, each
        carrying the round's tokens split evenly and cost_usd=0.0. These rows are
        an attribution BREAKDOWN of the authoritative loop-level usage (recorded
        once via budget.record_usage with the full per-round tokens), NOT
        independent totals. Summing TokenUsage tokens across both the per-tool
        rows and the loop-level row therefore double-counts (ORCH-P2-1): any
        token aggregate must exclude the tool:* breakdown rows.
        """
        from src.models.token_usage import TokenUsage
        from src.orchestrator.agent_loop import agent_loop

        two_tools = FakeResponse(
            [
                FakeToolUseBlock("t1", "search_memory", {"query": "a"}),
                FakeToolUseBlock("t2", "list_memories", {"query": "b"}),
            ]
        )
        two_tools.usage = FakeUsage(input_tokens=100, output_tokens=40)
        # Isolate the round under test: the trailing text round contributes 0 so
        # the loop-level total equals exactly the per-tool round's tokens.
        text_done = make_text_response("Done")
        text_done.usage = FakeUsage(input_tokens=0, output_tokens=0)
        client.messages.create = AsyncMock(side_effect=[two_tools, text_done])

        added_records = []

        class CapturingDB:
            def add(self, obj):
                added_records.append(obj)

            async def commit(self):
                pass

        class CapturingFactory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return CapturingDB()

            async def __aexit__(self, *args):
                pass

        budget = _make_budget()
        await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[
                    {"name": "search_memory", "description": "s", "input_schema": {}},
                    {"name": "list_memories", "description": "l", "input_schema": {}},
                ],
                message="multi",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=CapturingFactory(),
                services=MagicMock(),
                budget=budget,
                trace=trace,
                execute_tool_fn=AsyncMock(return_value={"results": []}),
            )
        )

        tool_rows = [r for r in added_records if isinstance(r, TokenUsage)]
        # Two tool_use blocks -> two per-tool breakdown rows, tokens split evenly.
        assert len(tool_rows) == 2
        for r in tool_rows:
            assert r.input_tokens == 50  # 100 // 2
            assert r.output_tokens == 20  # 40 // 2
            assert r.cost_usd == 0.0  # breakdown rows carry no cost (cost lives on the loop row)
            assert r.trigger.startswith("tool:")
        assert {r.trigger for r in tool_rows} == {"tool:search_memory", "tool:list_memories"}

        # The authoritative total is recorded once at the loop level with the full
        # round tokens — so per-tool rows (sum=100) + loop row (100) double-count.
        budget.record_usage.assert_called_once()
        loop_kwargs = budget.record_usage.call_args[1]
        assert loop_kwargs["input_tokens"] == 100
        assert loop_kwargs["output_tokens"] == 40

    async def test_governor_forces_verdict_tool_choice(self, client, trace):
        """When the governor's structured-output verdict tool is available, the
        loop forces tool_choice to it and disables thinking (forced tool_choice
        is incompatible with thinking). Pins the structured-output behavior so it
        survives replacing the agent-name sniff with verdict-tool detection."""
        from src.orchestrator.agent_loop import agent_loop

        gov = FakeSubAgent(name="governor")
        gov.thinking = FakeThinkingConfig(enabled=True)

        client.messages.create = AsyncMock(return_value=make_text_response("{}"))

        await _collect_events(
            agent_loop(
                client=client,
                agent=gov,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "report_governor_verdict", "description": "v", "input_schema": {}}],
                message="evaluate",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(return_value={"ok": True}),
            )
        )

        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["tool_choice"] == {
            "type": "tool",
            "name": "report_governor_verdict",
        }
        assert "thinking" not in call_kwargs

    async def test_non_governor_does_not_force_tool_choice(self, client, agent, trace):
        """A non-governor agent without the verdict tool never forces tool_choice."""
        from src.orchestrator.agent_loop import agent_loop

        client.messages.create = AsyncMock(return_value=make_text_response("done"))

        await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "search_memory", "description": "s", "input_schema": {}}],
                message="hi",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(return_value={"ok": True}),
            )
        )

        call_kwargs = client.messages.create.call_args.kwargs
        assert "tool_choice" not in call_kwargs

    async def test_out_of_scope_tool_rejected(self, client, trace):
        """Agent calling a tool whose capability is NOT in its scope is rejected
        with is_error, and execute_tool_fn is NOT called (FIX #1, fail-closed)."""
        from src.orchestrator.agent_loop import LoopToolResult, agent_loop

        # Presenter-like agent: read/respond scope, no write capabilities.
        presenter = FakeSubAgent(name="presenter", capability_scope={"internal.search"})

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("gmail_send_email", {"to": "x@y.com"}),
                make_text_response("Done"),
            ]
        )

        tool_fn = AsyncMock(return_value={"ok": True})

        # Registry resolves gmail_send_email → capability email.send (out of scope)
        fake_tool = MagicMock()
        fake_tool.capability = "email.send"
        registry = MagicMock()
        registry.get_tool = AsyncMock(return_value=fake_tool)

        with patch("src.orchestrator.agent_loop.ToolRegistry", return_value=registry):
            events = await _collect_events(
                agent_loop(
                    client=client,
                    agent=presenter,
                    model="claude-sonnet-4-20250514",
                    system_blocks=[],
                    tools=[{"name": "gmail_send_email", "description": "Send", "input_schema": {}}],
                    message="Send email",
                    user_id="usr_test",
                    workspace_id="ws_test",
                    db_factory=_make_db_factory(),
                    services=MagicMock(),
                    budget=_make_budget(),
                    trace=trace,
                    execute_tool_fn=tool_fn,
                )
            )

        blocked_results = [e for e in events if isinstance(e, LoopToolResult) and e.blocked]
        assert len(blocked_results) == 1
        assert "scope" in str(blocked_results[0].result).lower()
        tool_fn.assert_not_called()

    async def test_in_scope_tool_proceeds(self, client, trace):
        """Agent calling a tool whose capability IS in scope proceeds normally."""
        from src.orchestrator.agent_loop import LoopToolResult, agent_loop

        perceiver = FakeSubAgent(name="perceiver", capability_scope={"internal.search"})

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("search_memory", {"query": "x"}),
                make_text_response("Found"),
            ]
        )

        tool_fn = AsyncMock(return_value={"results": []})

        fake_tool = MagicMock()
        fake_tool.capability = "internal.search"
        registry = MagicMock()
        registry.get_tool = AsyncMock(return_value=fake_tool)

        with patch("src.orchestrator.agent_loop.ToolRegistry", return_value=registry):
            events = await _collect_events(
                agent_loop(
                    client=client,
                    agent=perceiver,
                    model="claude-sonnet-4-20250514",
                    system_blocks=[],
                    tools=[{"name": "search_memory", "description": "Search", "input_schema": {}}],
                    message="Search",
                    user_id="usr_test",
                    workspace_id="ws_test",
                    db_factory=_make_db_factory(),
                    services=MagicMock(),
                    budget=_make_budget(),
                    trace=trace,
                    execute_tool_fn=tool_fn,
                )
            )

        blocked = [e for e in events if isinstance(e, LoopToolResult) and e.blocked]
        assert len(blocked) == 0
        tool_fn.assert_called_once()

    async def test_secret_redacted_in_persisted_span(self, client, trace):
        """FIX #3: secrets in tool output are redacted in persisted SpanToolCall,
        but the live result returned to the loop is unchanged."""
        from src.orchestrator.agent_loop import LoopDone, LoopToolResult, agent_loop

        agent = FakeSubAgent(name="perceiver", capability_scope={"internal.search"})

        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("oauth_tool", {}),
                make_text_response("Done"),
            ]
        )

        secret_result = {
            "access_token": "abcdef1234567890SECRET",
            "password": "hunter2hunter2",
            "data": "safe",
        }
        tool_fn = AsyncMock(return_value=secret_result)

        fake_tool = MagicMock()
        fake_tool.capability = "internal.search"
        registry = MagicMock()
        registry.get_tool = AsyncMock(return_value=fake_tool)

        with patch("src.orchestrator.agent_loop.ToolRegistry", return_value=registry):
            events = await _collect_events(
                agent_loop(
                    client=client,
                    agent=agent,
                    model="claude-sonnet-4-20250514",
                    system_blocks=[],
                    tools=[{"name": "oauth_tool", "description": "OAuth", "input_schema": {}}],
                    message="Auth",
                    user_id="usr_test",
                    workspace_id="ws_test",
                    db_factory=_make_db_factory(),
                    services=MagicMock(),
                    budget=_make_budget(),
                    trace=trace,
                    execute_tool_fn=tool_fn,
                )
            )

        # Live result returned to the loop is UNCHANGED
        tool_results = [e for e in events if isinstance(e, LoopToolResult)]
        assert len(tool_results) == 1
        assert tool_results[0].result == secret_result

        # Persisted span output_data is redacted
        done = [e for e in events if isinstance(e, LoopDone)]
        assert len(done) == 1
        span_calls = done[0].tool_call_details
        assert len(span_calls) == 1
        persisted = span_calls[0].output_data
        persisted_str = str(persisted)
        assert "abcdef1234567890SECRET" not in persisted_str
        assert "hunter2hunter2" not in persisted_str
        assert "REDACTED" in persisted_str
        assert "safe" in persisted_str

    async def test_tool_token_usage_divided_across_multiple_tools(self, client, agent, trace):
        """When multiple tools called in one response, tokens are divided equally."""
        from src.orchestrator.agent_loop import agent_loop

        multi_tool_response = FakeResponse(
            [
                FakeToolUseBlock("t1", "search_memory", {"query": "a"}),
                FakeToolUseBlock("t2", "search_memory", {"query": "b"}),
            ],
            stop_reason="tool_use",
        )
        multi_tool_response.usage = FakeUsage(input_tokens=100, output_tokens=40)
        multi_tool_response.usage.cache_creation_input_tokens = 0
        multi_tool_response.usage.cache_read_input_tokens = 0

        client.messages.create = AsyncMock(
            side_effect=[multi_tool_response, make_text_response("Done")]
        )

        added_records = []

        class CapturingDB:
            def add(self, obj):
                added_records.append(obj)

            async def commit(self):
                pass

        class CapturingFactory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return CapturingDB()

            async def __aexit__(self, *args):
                pass

        await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[{"name": "search_memory", "description": "Search", "input_schema": {}}],
                message="Search test",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=CapturingFactory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(return_value={"results": []}),
            )
        )

        from src.models.token_usage import TokenUsage

        tool_usages = [r for r in added_records if isinstance(r, TokenUsage)]
        assert len(tool_usages) == 2, f"Expected 2 tool TokenUsage records, got {len(tool_usages)}"
        # Each tool gets 100//2=50 input, 40//2=20 output
        for record in tool_usages:
            assert record.input_tokens == 50, f"Expected 50 input_tokens, got {record.input_tokens}"
            assert record.output_tokens == 20, (
                f"Expected 20 output_tokens, got {record.output_tokens}"
            )


# ── Thinking-fallback paths ──────────────────────────────────────────────


@dataclass
class _ThinkingAgent(FakeSubAgent):
    """SubAgent with extended thinking enabled (the precondition for fallback)."""

    thinking: FakeThinkingConfig = field(
        default_factory=lambda: FakeThinkingConfig(enabled=True, budget_tokens=2048)
    )


class _FailingStreamCM:
    """Async context manager whose __aenter__ raises — simulates a stream that never
    yields a final message (response stays None, triggering the fallback branch)."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *args):
        return False


def _thinking_error() -> Exception:
    """An error _is_thinking_error() classifies as a thinking-block incompatibility."""
    return Exception(
        "messages.1.content.0.thinking: thinking blocks are not supported in this configuration"
    )


def _transient_error() -> Exception:
    """A non-thinking transient error — must NOT trigger thinking stripping."""
    return Exception("connection reset by peer")


def _sequenced_create(steps: list, captured_thinking: list[bool]):
    """Build an async create() that records whether 'thinking' was in kwargs at call
    time, then raises/returns the next queued step."""
    queue = list(steps)

    async def _create(**kwargs):
        captured_thinking.append("thinking" in kwargs)
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return _create


class TestThinkingFallback:
    """Covers the three thinking-fallback branches in agent_loop (agent_loop.py:362-405).

    Sanity for the headline 'double-strip' path: once the *stream* fails with a thinking
    error, thinking is disabled before the fallback runs, so the nested fallback-strip
    (lines 374-386) is only reachable when the stream fails for a *transient* reason and
    the *fallback* then surfaces the thinking incompatibility. test_stream_transient_then_
    fallback_thinking_error exercises exactly that path.
    """

    @pytest.fixture
    def agent(self):
        return _ThinkingAgent()

    @pytest.fixture
    def trace(self):
        t = MagicMock()
        t.trace_id = "trace_test"
        t.trigger = "test"
        span = MagicMock()
        span.span_id = "span_test"
        t.start_span.return_value = span
        return t

    async def _run(self, client, agent, trace, *, stream: bool):
        from src.orchestrator.agent_loop import agent_loop

        return await _collect_events(
            agent_loop(
                client=client,
                agent=agent,
                model="claude-sonnet-4-20250514",
                system_blocks=[],
                tools=[],
                message="Hi",
                user_id="usr_test",
                workspace_id="ws_test",
                db_factory=_make_db_factory(),
                services=MagicMock(),
                budget=_make_budget(),
                trace=trace,
                execute_tool_fn=AsyncMock(),
                stream=stream,
            )
        )

    async def test_nonstream_thinking_error_strips_and_retries(self, trace):
        """Non-stream path: create() fails with a thinking error → thinking is stripped
        and the call is retried without thinking → loop recovers."""
        from src.orchestrator.agent_loop import LoopDone

        client = AsyncMock()
        captured: list[bool] = []
        client.messages.create = _sequenced_create(
            [_thinking_error(), make_text_response("recovered")], captured
        )

        events = await self._run(client, _ThinkingAgent(), trace, stream=False)

        done = [e for e in events if isinstance(e, LoopDone)]
        assert len(done) == 1 and done[0].text == "recovered"
        # First call carried thinking; the retry dropped it.
        assert captured == [True, False]

    async def test_stream_thinking_error_falls_back_without_thinking(self, trace):
        """Streaming path: the stream itself raises a thinking error → thinking disabled,
        first (and only) fallback create() succeeds with thinking stripped."""
        from src.orchestrator.agent_loop import LoopDone

        client = AsyncMock()
        client.messages.stream = MagicMock(return_value=_FailingStreamCM(_thinking_error()))
        captured: list[bool] = []
        client.messages.create = _sequenced_create([make_text_response("recovered")], captured)

        events = await self._run(client, _ThinkingAgent(), trace, stream=True)

        done = [e for e in events if isinstance(e, LoopDone)]
        assert len(done) == 1 and done[0].text == "recovered"
        # Stream error already disabled thinking, so the single fallback call has none.
        assert captured == [False]

    async def test_stream_transient_then_fallback_thinking_error(self, trace):
        """Headline 'double-strip' path: the stream fails for a transient reason (thinking
        stays on), the fallback create() then surfaces a thinking error → thinking is
        stripped and a final create() succeeds. Exercises agent_loop.py:374-386."""
        from src.orchestrator.agent_loop import LoopDone

        client = AsyncMock()
        client.messages.stream = MagicMock(return_value=_FailingStreamCM(_transient_error()))
        captured: list[bool] = []
        client.messages.create = _sequenced_create(
            [_thinking_error(), make_text_response("recovered")], captured
        )

        events = await self._run(client, _ThinkingAgent(), trace, stream=True)

        done = [e for e in events if isinstance(e, LoopDone)]
        assert len(done) == 1 and done[0].text == "recovered"
        # Transient stream error kept thinking on for the first fallback; the second
        # fallback (after the thinking-strip) dropped it.
        assert captured == [True, False]

    async def test_stream_transient_then_nonthinking_error_propagates(self, trace):
        """A transient stream failure followed by a non-thinking fallback error must NOT
        be silently swallowed — the loop surfaces a LoopError rather than a LoopDone."""
        from src.orchestrator.agent_loop import LoopDone, LoopError

        client = AsyncMock()
        client.messages.stream = MagicMock(return_value=_FailingStreamCM(_transient_error()))
        captured: list[bool] = []
        client.messages.create = _sequenced_create([_transient_error()], captured)

        events = await self._run(client, _ThinkingAgent(), trace, stream=True)

        assert any(isinstance(e, LoopError) for e in events)
        assert not any(isinstance(e, LoopDone) and e.text == "recovered" for e in events)


class TestUnavailableServerShortCircuit:
    """Per-loop resilience: once an MCP server returns auth_required (or the
    legacy "bridge not initialized" shape), the loop must stop burning turns
    calling that server's tools. Analogous to AnthropicCircuitBreaker, but
    per-invocation and reset every call (no cross-run poisoning)."""

    @pytest.fixture(autouse=True)
    def _default_in_scope_registry(self):
        from unittest.mock import patch as _patch

        fake_tool = MagicMock()
        fake_tool.capability = "test.cap"
        registry = MagicMock()
        registry.get_tool = AsyncMock(return_value=fake_tool)
        with _patch("src.orchestrator.agent_loop.ToolRegistry", return_value=registry):
            yield

    @pytest.fixture
    def trace(self):
        t = MagicMock()
        t.trace_id = "trace_test"
        t.trigger = "test"
        span = MagicMock()
        span.span_id = "span_test"
        t.start_span.return_value = span
        return t

    def _kwargs(self, client, tool_fn, tools, trace):
        return dict(
            client=client,
            agent=FakeSubAgent(),
            model="claude-sonnet-4-20250514",
            system_blocks=[],
            tools=tools,
            message="go",
            user_id="usr_test",
            workspace_id="ws_test",
            db_factory=_make_db_factory(),
            services=MagicMock(),
            budget=_make_budget(),
            trace=trace,
            execute_tool_fn=tool_fn,
        )

    async def test_auth_required_short_circuits_same_server_in_same_turn(self, trace):
        """First gmail tool returns auth_required; the second gmail tool in the
        SAME assistant turn is short-circuited — execute_tool_fn is NOT called
        for it (the burn-3-turns case)."""
        from src.orchestrator.agent_loop import LoopToolResult, agent_loop

        # One assistant turn requesting two gmail tools, then a final text turn.
        client = AsyncMock()
        client.messages.create = AsyncMock(
            side_effect=[
                make_multi_tool_response(
                    ("search_gmail_messages", {"q": "x"}),
                    ("list_gmail_labels", {}),
                ),
                make_text_response("told the user to reconnect"),
            ]
        )

        auth_err = {
            "status": "error",
            "error": "needs reconnect",
            "error_code": "auth_required",
            "provider": "google",
            "server": "google-workspace",
        }
        tool_fn = AsyncMock(return_value=auth_err)

        events = await _collect_events(
            agent_loop(
                **{
                    **self._kwargs(client, tool_fn, None, trace),
                    "tools": [
                        {"name": "search_gmail_messages", "description": "S", "input_schema": {}},
                        {"name": "list_gmail_labels", "description": "L", "input_schema": {}},
                    ],
                }
            )
        )

        # execute_tool_fn called exactly once (the first gmail tool); the second
        # was short-circuited.
        assert tool_fn.call_count == 1
        assert tool_fn.call_args_list[0].args[0] == "search_gmail_messages"

        # Both tools still produce a tool_result event (the short-circuited one
        # carries the cached auth error + terminal steer).
        results = [e for e in events if isinstance(e, LoopToolResult)]
        assert len(results) == 2
        short_circuited = results[1]
        assert short_circuited.tool_name == "list_gmail_labels"
        assert "auth_required" in str(short_circuited.result.get("error_code"))

        # The terminal steer message must reach the model as is_error on BOTH
        # tool results sent back in the user turn.
        second_call_messages = client.messages.create.call_args_list[1][1]["messages"]
        tool_result_msg = second_call_messages[-1]
        assert tool_result_msg["role"] == "user"
        for block in tool_result_msg["content"]:
            assert block.get("is_error") is True
            assert "re-authoriz" in block["content"].lower()
            assert "do not retry" in block["content"].lower()

    async def test_legacy_bridge_not_initialized_yields_terminal_message(self, trace):
        """The legacy {"error": "MCP bridge not initialized"} shape gets the same
        terminal 'do not retry' steer appended to the tool result."""
        from src.orchestrator.agent_loop import agent_loop

        client = AsyncMock()
        client.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("search_gmail_messages", {"q": "x"}),
                make_text_response("done"),
            ]
        )
        tool_fn = AsyncMock(return_value={"status": "error", "error": "MCP bridge not initialized"})

        await _collect_events(
            agent_loop(
                **{
                    **self._kwargs(client, tool_fn, None, trace),
                    "tools": [
                        {"name": "search_gmail_messages", "description": "S", "input_schema": {}}
                    ],
                }
            )
        )

        second_call_messages = client.messages.create.call_args_list[1][1]["messages"]
        tool_result_msg = second_call_messages[-1]
        block = tool_result_msg["content"][0]
        assert block.get("is_error") is True
        assert "do not retry" in block["content"].lower()
        content_lower = block["content"].lower()
        assert "unavailable" in content_lower or "re-authoriz" in content_lower

    async def test_unavailable_set_resets_between_invocations(self, trace):
        """A server marked unavailable in one agent_loop call must NOT poison a
        later, separate invocation — the set is per-call, not module-global."""
        from src.orchestrator.agent_loop import agent_loop

        # ── Run 1: gmail returns auth_required, poisoning 'google' for this run.
        client1 = AsyncMock()
        client1.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("search_gmail_messages", {"q": "x"}),
                make_text_response("steered"),
            ]
        )
        run1_fn = AsyncMock(
            return_value={
                "status": "error",
                "error": "needs reconnect",
                "error_code": "auth_required",
                "provider": "google",
                "server": "google-workspace",
            }
        )
        await _collect_events(
            agent_loop(
                **{
                    **self._kwargs(client1, run1_fn, None, trace),
                    "tools": [
                        {"name": "search_gmail_messages", "description": "S", "input_schema": {}}
                    ],
                }
            )
        )

        # ── Run 2: a SEPARATE invocation calls a gmail tool. It must execute
        # (server recovered / fresh run) — NOT be short-circuited.
        client2 = AsyncMock()
        client2.messages.create = AsyncMock(
            side_effect=[
                make_tool_response("list_gmail_labels", {}),
                make_text_response("ok"),
            ]
        )
        run2_fn = AsyncMock(return_value={"labels": ["INBOX"], "status": "ok"})
        await _collect_events(
            agent_loop(
                **{
                    **self._kwargs(client2, run2_fn, None, trace),
                    "tools": [
                        {"name": "list_gmail_labels", "description": "L", "input_schema": {}}
                    ],
                }
            )
        )

        # Run 2's tool actually executed — no cross-run poisoning.
        assert run2_fn.call_count == 1
        assert run2_fn.call_args_list[0].args[0] == "list_gmail_labels"


class TestUnavailableServerResolvedByRegistry:
    """C5: the short-circuit must key off the tool's REGISTERED server name, not
    a substring of the raw tool name.

    A Google tool like ``search_messages`` does not contain "gmail"/"google" in
    its name, so ``provider_for_server(tool_name)`` resolves it to itself and the
    old provider-name short-circuit never fired — the tool was re-invoked against
    dead auth (the exact budget burn this guard exists to prevent). With the
    registry-server fix, the first tool's auth_required envelope marks its
    *server* (``google-workspace``) unavailable, and the second tool — resolved
    via the registry to the same server — is short-circuited even though its name
    shares no substring with the provider.
    """

    @pytest.fixture
    def trace(self):
        t = MagicMock()
        t.trace_id = "trace_test"
        t.trigger = "test"
        span = MagicMock()
        span.span_id = "span_test"
        t.start_span.return_value = span
        return t

    def _registry_for(self, server_by_tool: dict):
        """A ToolRegistry double whose get_tool returns a tool def carrying the
        tool's capability (in scope) AND its registered server name."""

        async def _get_tool(name):
            tool = MagicMock()
            tool.capability = "test.cap"
            tool.server = server_by_tool.get(name)
            return tool

        registry = MagicMock()
        registry.get_tool = AsyncMock(side_effect=_get_tool)
        return registry

    def _kwargs(self, client, tool_fn, tools, trace):
        return dict(
            client=client,
            agent=FakeSubAgent(),
            model="claude-sonnet-4-20250514",
            system_blocks=[],
            tools=tools,
            message="go",
            user_id="usr_test",
            workspace_id="ws_test",
            db_factory=_make_db_factory(),
            services=MagicMock(),
            budget=_make_budget(),
            trace=trace,
            execute_tool_fn=tool_fn,
        )

    async def test_substringless_google_tool_short_circuits_via_server(self, trace):
        from src.orchestrator.agent_loop import LoopToolResult, agent_loop

        # Two Google tools whose NAMES contain no provider substring.
        server_by_tool = {
            "search_messages": "google-workspace",
            "list_threads": "google-workspace",
        }
        registry = self._registry_for(server_by_tool)

        client = AsyncMock()
        client.messages.create = AsyncMock(
            side_effect=[
                make_multi_tool_response(
                    ("search_messages", {"q": "x"}),
                    ("list_threads", {}),
                ),
                make_text_response("told the user to reconnect"),
            ]
        )

        auth_err = {
            "status": "error",
            "error": "needs reconnect",
            "error_code": "auth_required",
            "provider": "google",
            "server": "google-workspace",
        }
        tool_fn = AsyncMock(return_value=auth_err)

        with patch("src.orchestrator.agent_loop.ToolRegistry", return_value=registry):
            events = await _collect_events(
                agent_loop(
                    **{
                        **self._kwargs(client, tool_fn, None, trace),
                        "tools": [
                            {"name": "search_messages", "description": "S", "input_schema": {}},
                            {"name": "list_threads", "description": "L", "input_schema": {}},
                        ],
                    }
                )
            )

        # execute_tool_fn called exactly ONCE — the second Google tool (same
        # server) was short-circuited despite its name lacking any provider
        # substring. This is the C5 fix; pre-fix tool_fn.call_count == 2.
        assert tool_fn.call_count == 1
        assert tool_fn.call_args_list[0].args[0] == "search_messages"

        results = [e for e in events if isinstance(e, LoopToolResult)]
        assert len(results) == 2
        short_circuited = results[1]
        assert short_circuited.tool_name == "list_threads"
        assert "auth_required" in str(short_circuited.result.get("error_code"))

    async def test_unrelated_server_tool_still_executes(self, trace):
        """A tool on a DIFFERENT server is not short-circuited by another
        server's auth failure."""
        from src.orchestrator.agent_loop import agent_loop

        server_by_tool = {
            "search_messages": "google-workspace",
            "create_issue": "github",
        }
        registry = self._registry_for(server_by_tool)

        client = AsyncMock()
        client.messages.create = AsyncMock(
            side_effect=[
                make_multi_tool_response(
                    ("search_messages", {"q": "x"}),
                    ("create_issue", {"title": "t"}),
                ),
                make_text_response("done"),
            ]
        )

        async def _tool(name, *a, **k):
            if name == "search_messages":
                return {
                    "status": "error",
                    "error_code": "auth_required",
                    "provider": "google",
                    "server": "google-workspace",
                }
            return {"status": "ok", "result": "created"}

        tool_fn = AsyncMock(side_effect=_tool)

        with patch("src.orchestrator.agent_loop.ToolRegistry", return_value=registry):
            await _collect_events(
                agent_loop(
                    **{
                        **self._kwargs(client, tool_fn, None, trace),
                        "tools": [
                            {"name": "search_messages", "description": "S", "input_schema": {}},
                            {"name": "create_issue", "description": "C", "input_schema": {}},
                        ],
                    }
                )
            )

        # Both tools executed — the github tool is on a different server.
        assert tool_fn.call_count == 2
        called = [c.args[0] for c in tool_fn.call_args_list]
        assert "create_issue" in called
