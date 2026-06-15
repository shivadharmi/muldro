"""Tests for chat SSE plan event (replaces decision event)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.contracts import MessageMetadata, PlanOutput, PlanStep


class TestMessageMetadataUsePlanOutput:
    """MessageMetadata.decision is now PlanOutput type."""

    def test_metadata_accepts_plan_output(self):
        plan = PlanOutput(
            goal="Check email",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Read",
                    capability="email.search",
                )
            ],
        )
        meta = MessageMetadata(
            trace_id="trace_1",
            decision=plan,
            agent_steps=[],
        )
        assert isinstance(meta.decision, PlanOutput)
        dumped = meta.model_dump(mode="json")
        assert dumped["decision"]["goal"] == "Check email"
        assert dumped["decision"]["steps"][0]["capability"] == "email.search"

    def test_metadata_decision_none(self):
        meta = MessageMetadata(trace_id="trace_1")
        assert meta.decision is None

    def test_metadata_round_trip_serialization(self):
        plan = PlanOutput(
            goal="Draft email",
            reasoning="User wants to draft",
            priority="high",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Draft",
                    capability="email.draft",
                    risk="medium",
                ),
            ],
        )
        meta = MessageMetadata(trace_id="t1", decision=plan)
        dumped = meta.model_dump(mode="json")
        restored = MessageMetadata.model_validate(dumped)
        assert restored.decision is not None
        assert restored.decision.goal == "Draft email"
        assert restored.decision.steps[0].capability == "email.draft"


# A secret-looking internal string that must never reach an SSE client frame.
STREAM_SECRET = "postgres://svc:p@ss@db.internal:5432/jarvis"


class TestProcessMessageStreamErrorIsSanitized:
    """When the orchestrator stream raises mid-flight, the client-facing
    `error` event must be the client-safe envelope (code/message/correlation_id)
    and never contain the raw exception string."""

    @pytest.mark.asyncio
    async def test_stream_error_event_has_safe_shape_no_leak(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)

        # Minimal trace manager: start returns an object with trace_id; finish is a noop.
        trace = MagicMock()
        trace.trace_id = "trace_stream_err"
        orch._trace_manager = MagicMock()
        orch._trace_manager.start_trace = MagicMock(return_value=trace)
        orch._trace_manager.finish_trace = AsyncMock()
        orch._client = MagicMock()
        orch._haiku_model = "claude-haiku"
        orch._spawn_background = MagicMock()
        orch._load_conversation_history = AsyncMock(return_value="")

        # classify_intent raises a leaky exception → caught by the stream's
        # except block, which must emit a sanitized frame.
        with patch(
            "src.orchestrator.jarvis.classify_intent",
            new_callable=AsyncMock,
            side_effect=ValueError(f"connection refused to {STREAM_SECRET}"),
        ):
            events = [
                evt
                async for evt in orch.process_message_stream(
                    message="hello",
                    user_id="usr_1",
                    workspace_id="ws_1",
                )
            ]

        error_events = [e for e in events if e.get("event") == "error"]
        assert error_events, f"expected an error event, got: {events}"
        err = error_events[-1]
        assert err["code"] == "internal_error"
        assert err["message"] == "Something went wrong. Please try again."
        assert err["correlation_id"]
        # No raw exception detail leaks into the client frame.
        assert STREAM_SECRET not in str(err)
        assert "connection refused" not in str(err)

    @pytest.mark.asyncio
    async def test_stream_validation_messages_are_controlled_and_safe(self):
        """Early validation messages are author-controlled (not exception text),
        so they are allowed to pass through verbatim."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)

        empty_uid = [
            evt
            async for evt in orch.process_message_stream(
                message="hi", user_id="", workspace_id="ws_1"
            )
        ]
        assert empty_uid == [{"event": "error", "message": "user_id and workspace_id are required"}]


class TestChatRouteUsesSafeErrorBoundary:
    """Regression guard: the SSE route must build its error frame from the
    central boundary, never from str(e)."""

    def test_route_source_uses_safe_error_event_not_str_e(self):
        import inspect

        from src.api import routes_chat

        src = inspect.getsource(routes_chat.chat_stream)
        # The except branch builds the frame via the boundary helper.
        assert "safe_error_event(e, get_correlation_id())" in src
        # And does NOT re-introduce the raw-exception frame.
        assert 'json.dumps({"event": "error", "message": str(e)})' not in src


class TestCallAgentStreamLoopErrorSanitized:
    """`_call_agent_stream` must sanitize LoopError frames — LoopError.message
    can carry a raw upstream exception string (agent_loop yields
    LoopError(message=str(e)))."""

    @pytest.mark.asyncio
    async def test_loop_error_frame_is_generic_no_leak(self):
        from src.orchestrator.agent_loop import LoopError
        from src.orchestrator.jarvis import JarvisOrchestrator

        leaky = f"anthropic 529 overloaded {STREAM_SECRET}"

        async def fake_agent_loop(**kwargs):
            yield LoopError(agent="presenter", message=leaky)

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        agent = MagicMock()
        orch._agents = {"presenter": agent}
        orch._get_model_for_agent = MagicMock(return_value="claude-haiku")
        orch._apply_cache_control_to_tools = MagicMock(return_value=[])
        orch._get_tools_for_agent = AsyncMock(return_value=[])
        orch._assemble_context = AsyncMock(return_value="")
        orch._build_system_prompt = MagicMock(return_value=[{"type": "text", "text": "x"}])
        orch._client = MagicMock()
        orch._db_factory = MagicMock()
        orch._services = MagicMock()
        orch._budget = MagicMock()
        orch._circuit_breaker = MagicMock()

        with patch("src.orchestrator.jarvis.agent_loop", side_effect=fake_agent_loop):
            events = [
                evt
                async for evt in orch._call_agent_stream(
                    "presenter",
                    message="go",
                    user_id="u1",
                    workspace_id="ws1",
                )
            ]

        err = [e for e in events if e.get("event") == "error"][-1]
        assert err["agent"] == "presenter"
        assert err["code"] == "internal_error"
        assert err["message"] == "Something went wrong. Please try again."
        assert err["correlation_id"]
        assert STREAM_SECRET not in str(err)
        assert "overloaded" not in str(err)
