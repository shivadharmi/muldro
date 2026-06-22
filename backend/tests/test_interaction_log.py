"""Tests for InteractionLog model."""

from unittest.mock import AsyncMock, MagicMock

import pytest

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


class TestLogInteraction:
    @pytest.mark.asyncio
    async def test_creates_interaction_log(self):
        from src.contracts import PlanOutput
        from src.orchestrator.plan_store import PlanStore

        mock_db = AsyncMock()
        mock_db_factory = MagicMock()
        mock_db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        store = PlanStore(lambda: mock_db_factory)

        ilog_id = await store.log_interaction(
            user_id="usr_01",
            workspace_id="ws_01",
            trace_id="trc_01",
            message_preview="Hello",
            intent="greeting",
            plan=PlanOutput(goal="Greet user", reasoning="Simple greeting"),
            conversation_id="conv_01",
        )

        assert ilog_id is not None
        assert ilog_id.startswith("ilog_")
        mock_db.add.assert_called_once()
        added = mock_db.add.call_args.args[0]
        assert added.__class__.__name__ == "InteractionLog"
        assert added.user_id == "usr_01"
        assert added.intent == "greeting"
        assert added.plan_summary == "Simple greeting"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self):
        from src.orchestrator.plan_store import PlanStore

        mock_db_factory = MagicMock()
        mock_db_factory.return_value.__aenter__ = AsyncMock(side_effect=Exception("DB down"))
        mock_db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        store = PlanStore(lambda: mock_db_factory)

        ilog_id = await store.log_interaction(
            user_id="usr_01",
            workspace_id="ws_01",
            trace_id="trc_01",
        )
        assert ilog_id is None

    @pytest.mark.asyncio
    async def test_truncates_long_previews(self):
        from src.orchestrator.plan_store import PlanStore

        mock_db = AsyncMock()
        mock_db_factory = MagicMock()
        mock_db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        store = PlanStore(lambda: mock_db_factory)

        long_message = "x" * 1000
        await store.log_interaction(
            user_id="usr_01",
            workspace_id="ws_01",
            trace_id="trc_01",
            message_preview=long_message,
        )

        added = mock_db.add.call_args.args[0]
        assert len(added.message_preview) == 500
