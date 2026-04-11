"""Tests for InteractionLog model."""

from src.models.interaction_log import InteractionLog


class TestInteractionLogModel:
    def test_required_fields(self):
        log = InteractionLog(
            interaction_id="ilog_01ABC",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_01",
        )
        assert log.interaction_id == "ilog_01ABC"
        assert log.workspace_id == "ws_01"
        assert log.input_tokens == 0
        assert log.output_tokens == 0
        assert log.cost_usd == 0.0
        assert log.latency_ms == 0

    def test_optional_fields(self):
        log = InteractionLog(
            interaction_id="ilog_02",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_02",
            conversation_id="conv_01",
            message_preview="Hello Jarvis",
            plan_summary="Simple greeting",
            plan_id="plan_01",
            run_id="run_01",
            intent="greeting",
            response_preview="Hi there!",
            input_tokens=150,
            output_tokens=50,
            cost_usd=0.002,
            latency_ms=320,
        )
        assert log.message_preview == "Hello Jarvis"
        assert log.intent == "greeting"
        assert log.cost_usd == 0.002

    def test_id_prefix(self):
        log = InteractionLog(
            interaction_id="ilog_01HXYZ",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_01",
        )
        assert log.interaction_id.startswith("ilog_")

    def test_no_status_field(self):
        """InteractionLog has no state machine — no status field."""
        log = InteractionLog(
            interaction_id="ilog_03",
            workspace_id="ws_01",
            user_id="usr_01",
            trace_id="trc_03",
        )
        assert not hasattr(log.__class__, "status") or "status" not in log.__table__.columns
