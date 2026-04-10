"""Tests for perception plan extraction, execution queuing, and idempotency.

Covers Phase 1 (_check_step_condition has_truthy_key),
Phase 3 (_queue_perception_plan), and Phase 7 (idempotency).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.contracts import PlanOutput, PlanStep

# ---------------------------------------------------------------------------
# _check_step_condition — Phase 1
# ---------------------------------------------------------------------------


class TestCheckStepCondition:
    """Verify _check_step_condition handles all condition types."""

    @staticmethod
    def _check(condition: dict, decision: dict) -> bool:
        from src.orchestrator.jarvis import JarvisOrchestrator

        return JarvisOrchestrator._check_step_condition(condition, decision)

    def test_has_truthy_key_with_value(self):
        """has_truthy_key returns True when key has a truthy value."""
        assert self._check(
            {"has_truthy_key": "plan_id"},
            {"plan_id": "plan_abc123"},
        )

    def test_has_truthy_key_with_none(self):
        """has_truthy_key returns False when key is None."""
        assert not self._check(
            {"has_truthy_key": "plan_id"},
            {"plan_id": None},
        )

    def test_has_truthy_key_with_empty_string(self):
        """has_truthy_key returns False when key is empty string."""
        assert not self._check(
            {"has_truthy_key": "plan_id"},
            {"plan_id": ""},
        )

    def test_has_truthy_key_missing_key(self):
        """has_truthy_key returns False when key is missing entirely."""
        assert not self._check(
            {"has_truthy_key": "plan_id"},
            {"decision": "create_task"},
        )

    def test_has_key(self):
        """has_key returns True when key exists (even if None)."""
        assert self._check(
            {"has_key": "plan_id"},
            {"plan_id": None},
        )

    def test_has_key_missing(self):
        assert not self._check(
            {"has_key": "plan_id"},
            {"decision": "create_task"},
        )

    def test_not_has_key(self):
        assert self._check(
            {"not_has_key": "plan_id"},
            {"decision": "create_task"},
        )

    def test_not_has_key_present(self):
        assert not self._check(
            {"not_has_key": "plan_id"},
            {"plan_id": "plan_123"},
        )

    def test_field_prefix(self):
        """field:<name> checks decision[name] == value."""
        assert self._check(
            {"field:decision": "create_task"},
            {"decision": "create_task"},
        )

    def test_field_prefix_mismatch(self):
        assert not self._check(
            {"field:decision": "create_task"},
            {"decision": "draft_reply"},
        )

    def test_direct_equality(self):
        """Fallback: key=value direct equality check."""
        assert self._check(
            {"decision": "create_task"},
            {"decision": "create_task"},
        )

    def test_direct_equality_mismatch(self):
        assert not self._check(
            {"decision": "create_task"},
            {"decision": "draft_reply"},
        )

    def test_multiple_conditions_all_pass(self):
        """All conditions must be satisfied (AND logic)."""
        assert self._check(
            {"has_truthy_key": "plan_id", "field:decision": "create_task"},
            {"plan_id": "plan_abc", "decision": "create_task"},
        )

    def test_multiple_conditions_one_fails(self):
        assert not self._check(
            {"has_truthy_key": "plan_id", "field:decision": "create_task"},
            {"plan_id": None, "decision": "create_task"},
        )


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

        # Should return original plan WITHOUT plan_id (skipped)
        assert result.plan_id is None

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
