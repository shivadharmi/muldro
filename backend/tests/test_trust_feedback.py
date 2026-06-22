"""Tests for trust feedback loop — record_approval_decision integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.risk_assessor import record_approval_decision


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


class TestRecordApprovalDecision:
    async def test_approved_increments_count(self, mock_db):
        state = MagicMock()
        state.approved_count = 0
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "first_use"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "approved")
        assert state.approved_count == 1
        assert state.last_decision_at is not None

    async def test_rejected_applies_demotion(self, mock_db):
        state = MagicMock()
        state.approved_count = 10
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "trusted"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "rejected")
        assert state.rejected_count == 1
        assert state.trust_level == "learning"
        assert state.cooldown_until is not None

    async def test_modified_increments_both(self, mock_db):
        state = MagicMock()
        state.approved_count = 5
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "learning"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "modified")
        assert state.modified_count == 1
        assert state.approved_count == 6

    async def test_graduation_after_three_approvals(self, mock_db):
        state = MagicMock()
        state.approved_count = 2
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "first_use"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "approved")
        assert state.trust_level == "learning"


class TestAutoExecutionTrustFeedback:
    """Successful auto-executed steps must reinforce trust (graduate), not only
    explicit user approvals — closes the autonomous outcome→trust loop."""

    async def test_auto_execution_success_records_approved(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        with patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec:
            await executor._record_auto_execution_outcome("email.send", "low", "ws_test")

        rec.assert_awaited_once()
        args = rec.await_args.args
        assert args[2] == "email.send"  # capability
        assert args[3] == "low"  # risk_level
        assert args[4] == "approved"  # decision

    async def test_empty_capability_skips(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        with patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec:
            await executor._record_auto_execution_outcome("", "low", "ws_test")

        rec.assert_not_awaited()

    def test_remember_auto_executed_records_on_checkpoint(self):
        """Auto-executed (capability, risk_level) pairs are stamped on the run
        so a later verification failure can reverse the trust reinforcement."""
        from unittest.mock import AsyncMock, MagicMock

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        run = MagicMock()
        run.checkpoint = None

        executor._remember_auto_executed(run, "email.send", "medium")
        executor._remember_auto_executed(run, "calendar.create", "low")

        assert run.checkpoint["auto_executed"] == [
            {"capability": "email.send", "risk_level": "medium"},
            {"capability": "calendar.create", "risk_level": "low"},
        ]


class TestVerificationIsAdvisory:
    """Verification is ADVISORY: a verdict is recorded for learning/visibility but
    never terminal-fails a completed run and never demotes trust. Only actually
    failed STEPS fail a run (handled in dag_runner, not here). This replaced the
    old gating behavior, which falsely failed 100% of verified runs (the verdict
    always evaluated to ``failed`` due to a status_equals/ordering bug) and would
    have falsely demoted trust on every auto-executed capability."""

    def _make_executor(self, verdict_value: str, step_statuses: list[str]):
        from unittest.mock import AsyncMock, MagicMock

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        verdict = MagicMock()
        verdict.value = verdict_value
        result = MagicMock()
        result.verdict = verdict
        result.score = 0.1
        result.details = "details"
        executor._verifier = MagicMock()
        executor._verifier.verify_run = AsyncMock(return_value=result)
        executor._checkpoint = AsyncMock()

        plan_res = MagicMock()
        plan_res.scalar_one_or_none.return_value = MagicMock(success_conditions=None)
        steps_res = MagicMock()
        steps_res.scalars.return_value.all.return_value = [
            MagicMock(status=s) for s in step_statuses
        ]

        async def _execute(stmt, *a, **k):
            # Route the run_verification step-status query to steps_res; everything
            # else (the plan lookup, store checkpoint queries) to plan_res.
            return steps_res if "task_steps" in str(stmt).lower() else plan_res

        executor._db.execute = AsyncMock(side_effect=_execute)
        return executor

    def _make_run(self, auto_executed):
        from unittest.mock import MagicMock

        run = MagicMock()
        run.run_id = "run_1"
        run.plan_id = "plan_1"
        run.workspace_id = "ws_1"
        run.status = "partially_completed"
        run.current_step_ids = []
        run.checkpoint = {"auto_executed": auto_executed} if auto_executed is not None else {}
        return run

    async def test_failed_verdict_does_not_demote_trust(self):
        from unittest.mock import AsyncMock, patch

        executor = self._make_executor("failed", ["completed"])
        run = self._make_run([{"capability": "email.send", "risk_level": "medium"}])

        with patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec:
            await executor._run_verification(run)

        rec.assert_not_awaited()

    async def test_failed_verdict_promotes_completed_run_not_failed(self):
        from unittest.mock import patch

        executor = self._make_executor("failed", ["completed", "completed"])
        run = self._make_run(None)

        with patch("src.services.outcome_learner.transition_run") as tr:
            await executor._run_verification(run)

        # Advisory: a failed verdict on an all-completed run promotes it to
        # completed — never to failed.
        tr.assert_called_once_with(run, "completed")

    async def test_failed_step_is_not_promoted_to_completed(self):
        from unittest.mock import patch

        executor = self._make_executor("failed", ["completed", "failed"])
        run = self._make_run(None)

        with patch("src.services.outcome_learner.transition_run") as tr:
            await executor._run_verification(run)

        # A run with a genuinely failed step is left for dag_runner to finalize —
        # verification neither promotes it to completed nor fails it.
        tr.assert_not_called()

    async def test_verdict_recorded_in_checkpoint(self):
        executor = self._make_executor("failed", ["completed"])
        run = self._make_run(None)

        await executor._run_verification(run)

        assert run.checkpoint["verification"]["verdict"] == "failed"


class TestCheckpointPreservation:
    """_checkpoint owns only the execution-snapshot keys; it must merge-preserve
    other application-state keys on run.checkpoint instead of clobbering them."""

    async def test_checkpoint_preserves_auto_executed(self):
        from unittest.mock import AsyncMock, MagicMock

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        executor._get_all_steps = AsyncMock(return_value=[])
        run = MagicMock()
        run.run_id = "run_1"
        run.workspace_id = "ws_1"
        run.status = "running"
        run.current_step_ids = ["s1"]
        run.checkpoint = {"auto_executed": [{"capability": "email.send", "risk_level": "low"}]}

        await executor._checkpoint(run, None, "step_completed")

        assert run.checkpoint["auto_executed"] == [
            {"capability": "email.send", "risk_level": "low"}
        ]
        assert run.checkpoint["status"] == "running"
        assert "checkpoint_at" in run.checkpoint
