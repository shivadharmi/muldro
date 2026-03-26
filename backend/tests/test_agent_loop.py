"""Tests for the unified agent loop (agent_loop.py).

Covers: basic flow, tool execution, timeouts, retries, error signaling,
thinking fallback, governor blocking, circuit breaker, and token tracking.
"""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

# ── Helpers ─────────────────────────────────────────────────────────────


@dataclass
class FakeThinkingConfig:
    enabled: bool = False
    budget_tokens: int = 4096


@dataclass
class FakeSubAgent:
    name: str = "test_agent"
    model_tier: str = "sonnet"
    capability_scope: set = field(default_factory=set)
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
