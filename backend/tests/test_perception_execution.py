"""Tests for perception plan extraction, execution queuing, and idempotency.

Covers _queue_perception_plan (Phase 3) and plan idempotency (Phase 7).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.contracts import PlanOutput, PlanStep

# ---------------------------------------------------------------------------
# _queue_perception_plan — Phase 3
# ---------------------------------------------------------------------------


class TestQueuePerceptionPlan:
    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_respond_only_plan_skipped(self, mock_client):
        """Plans with only respond/reason steps should not create background runs."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._db_factory = MagicMock()
        orch._settings = MagicMock()

        planner_text = (
            '{"goal": "Nothing important", "steps": ['
            '{"description": "Acknowledge", "capability": "respond"}'
            "]}"
        )
        result = await orch._queue_perception_plan(
            planner_text, "gmail", "usr_test", "ws_test", "trace_01"
        )

        # respond-only plan has no actionable steps — should return without queuing
        assert result is not None
        assert result.goal == "Nothing important"

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_system_capability_handled_inline(self, mock_client):
        """system.schedule_reminder steps should be handled inline."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._db_factory = MagicMock()
        orch._settings = MagicMock()
        orch._handle_system_capability = AsyncMock(return_value={})

        planner_text = (
            '{"goal": "Remind about call", "steps": ['
            '{"description": "Schedule reminder", '
            '"capability": "system.schedule_reminder"}'
            "]}"
        )
        result = await orch._queue_perception_plan(
            planner_text, "gmail", "usr_test", "ws_test", "trace_01"
        )

        # Should have been handled inline via _handle_system_capability
        orch._handle_system_capability.assert_awaited_once()
        assert result is not None

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_tool_steps_queue_background_run(self, mock_client):
        """Plans with tool steps should persist plan and create background run."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._settings = MagicMock()

        # Mock _persist_plan_record to return plan with plan_id
        plan_with_id = PlanOutput(
            goal="Send follow-up email",
            reasoning="Important investor",
            plan_id="plan_test_123",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Draft email",
                    capability="email.draft",
                    risk="medium",
                ),
            ],
        )
        orch._persist_plan_record = AsyncMock(return_value=plan_with_id)

        # Mock db_factory and graph executor
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=mock_cm)

        mock_run = MagicMock()
        mock_run.run_id = "run_test_123"
        mock_executor = AsyncMock()
        mock_executor.create_run = AsyncMock(return_value=mock_run)

        planner_text = (
            '{"goal": "Send follow-up email", '
            '"reasoning": "Important investor", '
            '"steps": [{"step_id": "s1", "description": "Draft email", '
            '"capability": "email.draft", "risk": "medium"}]}'
        )

        with patch(
            "src.services.graph_executor.create_graph_executor",
            new=AsyncMock(return_value=mock_executor),
        ):
            result = await orch._queue_perception_plan(
                planner_text, "gmail", "usr_test", "ws_test", "trace_01"
            )

        assert result is not None
        assert result.plan_id == "plan_test_123"
        orch._persist_plan_record.assert_awaited_once()
        # Verify trigger_type is "perception"
        call_kwargs = orch._persist_plan_record.call_args.kwargs
        assert call_kwargs.get("trigger_type") == "perception"


# ---------------------------------------------------------------------------
# _persist_plan_record — Phase 7 idempotency
# ---------------------------------------------------------------------------


class TestPersistPlanIdempotency:
    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_idempotency_prevents_duplicate_plans(self, mock_client):
        """Same idempotency_key should skip plan creation."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._settings = MagicMock()

        plan = PlanOutput(
            goal="Follow up with investor",
            reasoning="Important",
            steps=[PlanStep(step_id="s1", description="Send email", capability="email.send")],
        )

        # Mock DB that returns an existing plan for the idempotency key
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "plan_existing_123"
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=mock_cm)

        result = await orch._persist_plan_record(
            plan,
            "usr_test",
            "ws_test",
            trigger_type="perception",
            idempotency_key="perception:gmail:create_task:abc123",
        )

        # Idempotency: returns plan with the existing plan_id, nothing new persisted
        assert result.plan_id == "plan_existing_123"

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_trigger_type_perception(self, mock_client):
        """Perception plans should be persisted with trigger_type='perception'."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._settings = MagicMock()

        plan = PlanOutput(
            goal="Send report",
            reasoning="Scheduled",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Generate report",
                    capability="report.generate",
                ),
            ],
        )

        # Mock DB that returns no existing plan (idempotency check passes)
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        # First execute call: idempotency check (no match)
        mock_no_match = MagicMock()
        mock_no_match.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_no_match)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=mock_cm)

        result = await orch._persist_plan_record(
            plan,
            "usr_test",
            "ws_test",
            trigger_type="perception",
            idempotency_key="perception:gmail:create_task:hash123",
        )

        # Should have called db.add with the plan
        assert mock_db.add.called
        plan_arg = mock_db.add.call_args[0][0]
        assert plan_arg.trigger_type == "perception"
        assert plan_arg.idempotency_key == "perception:gmail:create_task:hash123"
        assert result.plan_id is not None
